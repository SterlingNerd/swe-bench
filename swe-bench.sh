#!/bin/bash

# ==============================================================================
# CONFIGURATION & CONSTANTS
# ==============================================================================
declare -A REPOS=(
    ["django__django-11039"]="https://github.com"
    ["scikit-learn__scikit-learn-10508"]="https://github.com"
    ["astropy__astropy-14995"]="https://github.com"
    ["pytest-dev__pytest-7407"]="https://github.com"
    ["requests__requests-3362"]="https://github.com"
)

declare -A PROMPTS=(
    ["django__django-11039"]="sqlmigrate should look at the database migration footprint rather than assuming a linear migration chain back to initial, causing crashes on multi-database setups using non-standard naming."
    ["scikit-learn__scikit-learn-10508"]="LabelEncoder fails when transform is called on an empty array or an array containing completely new string categories. It should raise a clear ValueError."
    ["astropy__astropy-14995"]="The NDDataRef mask initialization fails when a mask is passed as a constant operand during deep copy arithmetic. Needs basic type validation."
    ["pytest-dev__pytest-7407"]="pytest.approx fails when comparing complex numbers inside nested lists or tuple structures. It throws a TypeError instead of resolving the cell approximation."
    ["requests__requests-3362"]="json parameter in requests.request rejects unicode strings in Python 2/3 transitions when handling raw byte streams, throwing an encoding AttributeError."
)

# ==============================================================================
# FUNCTIONS
# ==============================================================================
show_help() {
    echo "Usage: ./compare_agents.sh [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --install   Install swebench python package via pip"
    echo "  --clone     Clone all 5 reference repositories into a ./repos directory"
    echo "  --prompts   Print out clean copy-paste prompts for your agents"
    echo "  --eval      Pack loose .patch files and execute the SWE-bench evaluation"
    echo "  --help      Show this help menu"
}

do_install() {
    echo "=== Installing SWE-bench ==="
    pip install swebench
}

do_clone() {
    echo "=== Cloning Repositories into ./repos/ ==="
    mkdir -p repos
    cd repos || exit 1
    for instance_id in "${!REPOS[@]}"; do
        repo_url=${REPOS[$instance_id]}
        # Strip instance ID tail to get clean directory name
        dir_name=$(echo "$instance_id" | awk -F'__' '{print $1}')
        if [ ! -d "$dir_name" ]; then
            echo "Cloning $dir_name..."
            git clone "$repo_url" "$dir_name"
        else
            echo "Directory $dir_name already exists. Skipping."
        fi
    done
    cd ..
}

do_prompts() {
    echo "=============================================================================="
    echo "AGENT BENCHMARK PROMPTS"
    echo "Instructions: Provide these to your local agent and your friend's agent."
    echo "Ensure they output a standard git diff block when they finish fixing it."
    echo "=============================================================================="
    for instance_id in "${!PROMPTS[@]}"; do
        echo -e "\n[Instance ID: $instance_id]"
        echo "------------------------------------------------------------------------------"
        echo "${PROMPTS[$instance_id]}"
        echo "------------------------------------------------------------------------------"
    done
}

do_eval() {
    echo "=== Packing Patches and Running Evaluation ==="

    # 1. Create scaffolding template if it doesn't exist
    if [ ! -d "patches" ]; then
        mkdir -p patches/local patches/friend
        echo "Created 'patches/local/' and 'patches/friend/' directories."
        echo "Drop your agent's loose .patch files in there named exactly by instance_id."
        echo "Example: patches/local/django__django-11039.patch"
        echo "Please populate these directories and re-run with --eval."
        exit 0
    fi

    # Helper function to convert raw patch files into the SWE-bench JSON structure
    package_json() {
        local source_dir=$1
        local output_file=$2
        local model_name=$3

        echo "[" > "$output_file"
        local first=true

        for instance_id in "${!PROMPTS[@]}"; do
            patch_file="$source_dir/$instance_id.patch"
            if [ -f "$patch_file" ]; then
                if [ "$first" = false ]; then
                    echo "," >> "$output_file"
                fi
                first=false

                # Escape backslashes and quotes for JSON safety
                patch_content=$(sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' "$patch_file" | awk '{printf "%s\\n", $0}')

                cat <<EOF >> "$output_file"
  {
    "instance_id": "$instance_id",
    "model_name_or_path": "$model_name",
    "model_patch": "$patch_content"
  }
EOF
            else
                echo "Warning: Missing patch file $patch_file"
            fi
        done
        echo -e "\n]" >> "$output_file"
    }

    # Package local and friend inputs into JSON
    package_json "patches/local" "local_predictions.json" "local_llm_agent"
    package_json "patches/friend" "friend_predictions.json" "friend_codex_agent"

    # 2. Execute evaluations inside SWE-bench via Docker
    if [ -f "local_predictions.json" ]; then
        echo "Running evaluation for Local LLM Agent..."
        python -m swebench.harness.run_evaluation \
            --dataset_name princeton-nlp/SWE-bench_Verified \
            --predictions_path ./local_predictions.json \
            --max_workers 2 \
            --namespace ""
    fi

    if [ -f "friend_predictions.json" ]; then
        echo "Running evaluation for Friend's Codex Agent..."
        python -m swebench.harness.run_evaluation \
            --dataset_name princeton-nlp/SWE-bench_Verified \
            --predictions_path ./friend_predictions.json \
            --max_workers 2 \
            --namespace ""
    fi
}

# ==============================================================================
# MAIN ARGUMENT PARSER
# ==============================================================================
if [ $# -eq 0 ]; then
    show_help
    exit 1
fi

while [ $# -gt 0 ]; do
    case "$1" in
        --install)
            do_install
            shift
            ;;
        --clone)
            do_clone
            shift
            ;;
        --prompts)
            do_prompts
            shift
            ;;
        --eval)
            do_eval
            shift
            ;;
        --help)
            show_help
            exit 0
            ;;
        *)
            echo "Unknown argument: $1"
            show_help
            exit 1
            ;;
    esac
done
