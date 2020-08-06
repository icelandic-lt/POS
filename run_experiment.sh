#!/bin/bash
DATA_DIR=./data
RAW_DIR="$DATA_DIR"/raw

NAME="$1"
# Move the arguments forward
shift

FIRST_STEP=1
LAST_STEP=1
if ((FIRST_STEP <= 1 && LAST_STEP >= 1)); then
    # dt=$(date '+%Y-%m-%d_%H-%M-%S');
    out_folder=./out/"$NAME"
    mkdir -p "$out_folder"
    extra_params="$*"
    sbatch \
    --output="$out_folder/slurm-%j.out" \
    --gres=gpu \
    --mem=10G \
    --wrap="./main.py \
    train-and-tag \
    $RAW_DIR/mim/10TM.plain \
    $RAW_DIR/mim/10PM.plain \
    $out_folder \
    --morphlex_embeddings_file data/extra/dmii.vectors_filtered \
    --word_embedding_lr 0.2 \
    --learning_rate 0.2 \
    --optimizer sgd \
    --epochs 1 \
    --batch_size 16 \
    --save_vocab \
    --save_model \
    --gpu \
    $extra_params"
fi