#!/usr/bin/env python
"""The main entrypoint to training and running a POS-tagger."""
from functools import reduce
from operator import add
import pickle
from pprint import pprint, pformat
import logging
import json
import pathlib
from typing import Dict

import click
from torch.utils.data.dataloader import DataLoader
from torch import save

from pos.data import (
    load_dicts,
    read_datasets,
    emb_pairs_to_dict,
    bin_str_to_emb_pair,
    read_morphlex,
    read_pretrained_word_embeddings,
    wemb_str_to_emb_pair,
    collate_fn,
    chunk_dataset,
    dechunk_dataset,
)
from pos import core
from pos.core import Vocab, FieldedDataset, Dicts, Fields, set_device, set_seed
from pos.model import (
    Decoder,
    Embedding,
    Encoder,
    Tagger,
    ABLTagger,
    TransformerEmbedding,
    PretrainedEmbedding,
    ClassingWordEmbedding,
    CharacterAsWordEmbedding,
    CharacterDecoder,
    Modules,
)
from pos.train import (
    MODULE_TO_FIELD,
    print_tagger,
    get_criterion,
    tag_data_loader,
    get_parameter_groups,
    get_optimizer,
    get_scheduler,
    run_epochs,
)
from pos.api import Tagger as api_tagger
from pos.utils import write_tsv
from pos import evaluate

DEBUG = False

MORPHLEX_VOCAB_PATH = "./data/extra/morphlex_vocab.txt"
PRETRAINED_VOCAB_PATH = "./data/extra/pretrained_vocab.txt"

log = logging.getLogger(__name__)


@click.group()
@click.option("--debug/--no-debug", default=False)
@click.option("--log/--no-log", default=False)
def cli(debug, log):  # pylint: disable=redefined-outer-name
    """Entrypoint to the program. --debug flag from command line is caught here."""
    log_level = logging.INFO
    if debug or log:
        log_level = logging.DEBUG
    logging.basicConfig(format="%(asctime)s - %(message)s", level=log_level)
    global DEBUG
    DEBUG = debug


@cli.command()
@click.argument("filepaths", nargs=-1)
@click.argument("output", type=click.File("w"))
@click.option(
    "--type",
    type=click.Choice(
        ["tags", "tokens", "lemmas", "morphlex", "pretrained"], case_sensitive=False
    ),
    default="tags",
)
def collect_vocabularies(filepaths, output, type):
    """Read all input files and extract relevant vocabulary."""
    result = []
    if type == "tags":
        ds = read_datasets(filepaths)
        result = list(Vocab.from_symbols(ds.get_field(Fields.GoldTags)))
    elif type == "tokens":
        ds = read_datasets(filepaths)
        result = list(Vocab.from_symbols(ds.get_field(Fields.Tokens)))
    elif type == "lemmas":
        ds = read_datasets(filepaths)
        result = list(Vocab.from_symbols(ds.get_field(Fields.GoldLemmas)))
    elif type == "morphlex":
        vocab_map, _ = read_morphlex(filepaths[0])
        result = list(vocab_map.w2i.keys())
    elif type == "pretrained":
        vocab_map, _ = read_pretrained_word_embeddings(filepaths[0])
        result = list(vocab_map.w2i.keys())
    for element in sorted(result):
        output.write(f"{element}\n")


@cli.command()
@click.argument("filepaths", nargs=-1)
@click.argument("embedding", default=None)
@click.argument("output", type=click.File("w+"))
@click.argument("emb_format", type=str)
def filter_embedding(filepaths, embedding, output, emb_format):
    """Filter an 'embedding' file based on the words which occur in the 'inputs' files.

    Result is written to the 'output' file with the same format as the embedding file.

    filepaths: Files to use for filtering (supports multiple files = globbing).
    Files should be .tsv, with two columns, the token and tag.
    embedding: The embedding file to filter.
    output: The file to write the result to.
    format: The format of the embedding file being read, 'bin' or 'wemb'.
    """
    log.info(f"Reading files={filepaths}")
    ds = read_datasets(filepaths)
    tokens = Vocab.from_symbols(ds.get_field())

    log.info(f"Number of tokens={len(tokens)}")
    with open(embedding) as f:
        if emb_format == "bin":
            emb_dict = emb_pairs_to_dict(f, bin_str_to_emb_pair)
            for token, value in emb_dict.items():
                if token in tokens:  # pylint: disable=unsupported-membership-test
                    output.write(f"{token};[{','.join((str(x) for x in value))}]\n")
        elif emb_format == "wemb":
            emb_dict = emb_pairs_to_dict(f, wemb_str_to_emb_pair)
            output.write("x x\n")
            for token, value in emb_dict.items():
                if token in tokens:  # pylint: disable=unsupported-membership-test
                    output.write(f"{token} {' '.join((str(x) for x in value))}\n")


# fmt: off
@cli.command()
@click.argument("training_files", nargs=-1)
@click.argument("test_file")
@click.argument("output_dir", default="./out")
@click.option("--gpu/--no_gpu", default=False)
@click.option("--save_model/--no_save_model", default=False)
@click.option("--save_vocab/--no_save_vocab", default=False)
@click.option("--tagger/--no_tagger", is_flag=True, default=False, help="Train tagger")
@click.option("--tagger_weight", default=1.0, help="Value to multiply tagging loss")
@click.option("--lemmatizer/--no_lemmatizer", is_flag=True, default=False, help="Train lemmatizer")
@click.option("--lemmatizer_weight", default=1.0, help="Value to multiply lemmatizer loss")
@click.option("--lemmatizer_char_dim", default=64, help="The character embedding dim.")
@click.option("--lemmatizer_char_attention/--no_lemmatizer_char_attention", default=True, help="Attend over characters?")
@click.option("--lemmatizer_teacher_forcing", default=0.0, help="Teacher forcing in Lemmatizer.")
@click.option("--known_chars_file", default=None, help="A file which contains the characters the model should know. File should be a single line, the line is split() to retrieve characters.",)
@click.option("--char_lstm_layers", default=0, help="The number of layers in character LSTM embedding. Set to 0 to disable.")
@click.option("--char_lstm_dim", default=128, help="The size of the hidden dim in character RNN.")
@click.option("--char_emb_dim", default=20, help="The embedding size for characters.")
@click.option("--morphlex_embeddings_file", default=None, help="A file which contains the morphological embeddings.")
@click.option("--morphlex_freeze", is_flag=True, default=True)
@click.option("--pretrained_word_embeddings_file", default=None, help="A file which contains pretrained word embeddings. See implementation for supported formats.")
@click.option("--word_embedding_dim", default=0, help="The word/token embedding dimension. Set to 0 to disable word embeddings.")
@click.option("--bert_encoder", default=None, help="A folder which contains a pretrained BERT-like model. Set to None to disable.")
@click.option("--main_lstm_layers", default=1, help="The number of bilstm layers to use in the encoder.")
@click.option("--main_lstm_dim", default=512, help="The dimension of the lstm to use in the encoder.")
@click.option("--emb_dropouts", default=0.0, help="The dropout to use for Embeddings.")
@click.option("--label_smoothing", default=0.0)
@click.option("--learning_rate", default=0.20)
@click.option("--epochs", default=20)
@click.option("--batch_size", default=32)
@click.option("--optimizer", default="sgd", type=click.Choice(["sgd", "adam"], case_sensitive=False), help="The optimizer to use.")
@click.option("--scheduler", default="multiply", type=click.Choice(["multiply", "plateau"], case_sensitive=False), help="The learning rate scheduler to use.")
# fmt: on
def train_and_tag(**kwargs):
    """Train a POS tagger and/or lemmatizer on intpus and write out the tagged test file.

    training_files: Files to use for training (supports multiple files = globbing).
    All training files should be .tsv, with two/three columns, the token, tag, lemma.
    test_file: Same format as training_files. Used to evaluate the model.
    output_dir: The directory to write out model and results.
    """
    pprint(kwargs)
    set_seed()
    set_device(gpu_flag=kwargs["gpu"])

    # Read train and test data

    unchunked_train_ds = read_datasets(
        kwargs["training_files"],
    )
    unchunked_test_ds = read_datasets(
        [kwargs["test_file"]],
    )
    # Set configuration values and create mappers
    embeddings, dicts = load_dicts(
        train_ds=unchunked_train_ds,
        pretrained_word_embeddings_file=kwargs["pretrained_word_embeddings_file"],
        morphlex_embeddings_file=kwargs["morphlex_embeddings_file"],
        known_chars_file=kwargs["known_chars_file"],
    )

    embs: Dict[Modules, Embedding] = {}
    if kwargs["bert_encoder"]:
        embs[Modules.BERT] = TransformerEmbedding(
            kwargs["bert_encoder"], dropout=kwargs["emb_dropouts"]
        )
        train_ds = chunk_dataset(
            unchunked_train_ds,
            embs[Modules.BERT].tokenizer,
            embs[Modules.BERT].max_length,
        )
        test_ds = chunk_dataset(
            unchunked_test_ds,
            embs[Modules.BERT].tokenizer,
            embs[Modules.BERT].max_length,
        )
    else:
        train_ds = unchunked_train_ds
        test_ds = unchunked_test_ds

    if kwargs["morphlex_embeddings_file"]:
        embs[Modules.MorphLex] = PretrainedEmbedding(
            vocab_map=dicts[Dicts.MorphLex],
            embeddings=embeddings[Dicts.MorphLex],
            freeze=True,
            dropout=kwargs["emb_dropouts"],
        )
    if kwargs["pretrained_word_embeddings_file"]:
        embs[Modules.Pretrained] = PretrainedEmbedding(
            vocab_map=dicts[Dicts.Pretrained],
            embeddings=embeddings[Dicts.Pretrained],
            freeze=True,
            dropout=kwargs["emb_dropouts"],
        )
    if kwargs["word_embedding_dim"]:
        embs[Modules.Trained] = ClassingWordEmbedding(
            dicts[Dicts.Tokens],
            kwargs["word_embedding_dim"],
            dropout=kwargs["emb_dropouts"],
        )
    if kwargs["char_lstm_layers"]:
        embs[Modules.CharactersToTokens] = CharacterAsWordEmbedding(
            dicts[Dicts.Chars],
            character_embedding_dim=kwargs["char_emb_dim"],
            char_lstm_layers=kwargs["char_lstm_layers"],
            char_lstm_dim=kwargs["char_lstm_dim"],  # we use the same dimension
            dropout=kwargs["emb_dropouts"],
        )
    encoder = Encoder(
        embeddings=embs,
        main_lstm_dim=kwargs["main_lstm_dim"],
        main_lstm_layers=kwargs["main_lstm_layers"],
        lstm_dropouts=0.0,
        input_dropouts=kwargs["emb_dropouts"],
    )
    decoders: Dict[Modules, Decoder] = {}
    if kwargs["tagger"]:
        log.info("Training Tagger")
        decoders[Modules.Tagger] = Tagger(
            vocab_map=dicts[Dicts.FullTag],
            input_dim=encoder.output_dim,
            weight=kwargs["tagger_weight"],
        )
    if kwargs["lemmatizer"]:
        log.info("Training Lemmatizer")
        decoders[Modules.Lemmatizer] = CharacterDecoder(
            vocab_map=dicts[Dicts.Chars],
            hidden_dim=encoder.output_dim,
            emb_dim=kwargs["lemmatizer_char_dim"],
            context_dim=encoder.output_dim,
            attention_dim=embs[Modules.CharactersToTokens].output_dim
            if Modules.CharactersToTokens in embs
            else 0,
            char_attention=Modules.CharactersToTokens in embs
            and kwargs["lemmatizer_char_attention"],
            char_rnn_inital=False,
            teacher_forcing=kwargs["lemmatizer_teacher_forcing"],
            dropout=kwargs["emb_dropouts"],
            weight=kwargs["lemmatizer_weight"],
        )
    abl_tagger = ABLTagger(encoder=encoder, decoders=decoders).to(core.device)

    # Train a model
    print_tagger(abl_tagger)

    train_dl = DataLoader(
        train_ds,
        collate_fn=collate_fn,  # type: ignore
        shuffle=True,
        batch_size=kwargs["batch_size"],
    )
    test_dl = DataLoader(
        test_ds,
        collate_fn=collate_fn,  # type: ignore
        shuffle=False,
        batch_size=kwargs["batch_size"] * 10,
    )
    criterion = get_criterion(
        decoders=decoders, label_smoothing=kwargs["label_smoothing"]
    )
    parameter_groups = get_parameter_groups(abl_tagger)
    log.info(
        f"Parameter groups: {tuple(len(group['params']) for group in parameter_groups)}"
    )
    optimizer = get_optimizer(
        parameter_groups, kwargs["optimizer"], kwargs["learning_rate"]
    )
    scheduler = get_scheduler(optimizer, kwargs["scheduler"])
    evaluators = {}
    if Modules.Tagger in decoders:
        evaluators[Modules.Tagger] = evaluate.TaggingEvaluation(
            test_dataset=test_ds,
            train_vocab=train_ds.get_vocab(),
            external_vocabs=evaluate.ExternalVocabularies(
                morphlex_tokens=Vocab.from_file(MORPHLEX_VOCAB_PATH),
                pretrained_tokens=Vocab.from_file(PRETRAINED_VOCAB_PATH),
            ),
        ).tagging_accuracy
    if Modules.Lemmatizer in decoders:
        evaluators[Modules.Lemmatizer] = evaluate.LemmatizationEvaluation(
            test_dataset=test_ds,
            train_vocab=train_ds.get_vocab(),
            train_lemmas=Vocab.from_symbols(train_ds.get_field(Fields.GoldLemmas)),
        ).lemma_accuracy

    # Write all configuration to disk
    output_dir = pathlib.Path(kwargs["output_dir"])
    write_hyperparameters(output_dir / "hyperparamters.json", (kwargs))

    # Start the training
    run_epochs(
        model=abl_tagger,
        optimizer=optimizer,
        criterion=criterion,
        scheduler=scheduler,
        evaluators=evaluators,
        train_data_loader=train_dl,
        test_data_loader=test_dl,
        epochs=kwargs["epochs"],
        output_dir=output_dir,
    )
    _, values = tag_data_loader(
        model=abl_tagger,
        data_loader=test_dl,
    )
    log.info("Writing predictions, dictionaries and model")
    for module_name, value in values.items():
        test_ds = test_ds.add_field(value, MODULE_TO_FIELD[module_name])
    # Dechunk - if we chunked
    if len(test_ds) != len(unchunked_test_ds):
        test_ds = dechunk_dataset(unchunked_test_ds, test_ds)

    test_ds.to_tsv_file(str(output_dir / "predictions.tsv"))

    with (output_dir / "known_toks.txt").open("w+") as f:
        for token in Vocab.from_symbols(train_ds.get_field(Fields.Tokens)):
            f.write(f"{token}\n")
    if Fields.GoldLemmas in train_ds.fields:
        with (output_dir / "known_lemmas.txt").open("w+") as f:
            for lemma in Vocab.from_symbols(train_ds.get_field(Fields.GoldLemmas)):
                f.write(f"{lemma}\n")
    if kwargs["save_vocab"]:
        save_location = output_dir.joinpath("dictionaries.pickle")
        with save_location.open("wb+") as f:
            pickle.dump(dicts, f)
    if kwargs["save_model"]:
        save_location = output_dir.joinpath("tagger.pt")
        save(abl_tagger, str(save_location))
    log.info("Done!")


def write_hyperparameters(path, hyperparameters):
    """Write hyperparameters to disk."""
    with path.open(mode="w") as f:
        json.dump(hyperparameters, f, indent=4)


@cli.command()
@click.argument("model_file")
@click.argument("data_in", type=str)
@click.argument("output", type=str)
@click.option(
    "--device", default="cpu", help="The device to use, 'cpu' or 'cuda:0' for GPU."
)
@click.option(
    "--contains_tags",
    is_flag=True,
    default=False,
    help="Does input data contain tags? Useful when predicting tags on a dataset which is already tagged (gold tags). All are written out: token\tgold\tpredicted",
)
def tag(model_file, data_in, output, device, contains_tags):
    """Tag tokens in a file.

    Args:
        model_file: A filepath to a trained model.
        dictionaries_file: A filepath to dictionaries (vocabulary mappings) for preprocessing.
        data_in: A filepath of a file formatted as: token per line, sentences separated with newlines (empty line).
        output: A filepath. Output is formatted like the input, but after each token there is a tab and then the tag.
    """
    tagger = api_tagger(model_file=model_file, device=device)
    log.info("Reading dataset")
    fields = (Fields.Tokens,)
    if contains_tags:
        fields = fields + (Fields.GoldTags,)
    ds = FieldedDataset.from_file(data_in, fields)
    chunked_ds = chunk_dataset(
        ds,
        tokenizer=tagger.model.encoder.embeddings[Modules.BERT.value].tokenizer,
        max_sequence_length=tagger.model.encoder.embeddings[
            Modules.BERT.value
        ].max_length,
    )
    predicted_tags = tagger.tag_bulk(dataset=chunked_ds, batch_size=16)
    chunked_ds = chunked_ds.add_field(predicted_tags, Fields.Tags)
    dechunk_ds = dechunk_dataset(ds, chunked_ds)
    log.info("Writing results")
    dechunk_ds.to_tsv_file(output)
    log.info("Done!")


# fmt: off
@cli.command()
@click.argument("predictions")
@click.argument("fields")
@click.option("--morphlex_vocab", help="The location of the morphlex vocabulary.", default=MORPHLEX_VOCAB_PATH)
@click.option("--pretrained_vocab", help="The location of the pretrained vocabulary.", default=PRETRAINED_VOCAB_PATH)
@click.option("--train_tokens", help="The location of the training tokens used to train the model.", default=None)
@click.option("--train_lemmas", help="The location of the training lemmas used to train the model.", default=None)
@click.option("--criteria", type=click.Choice(["accuracy", "profile"], case_sensitive=False), help="Which criteria to evaluate.", default="accuracy")
@click.option("--feature", type=click.Choice(["tags", "lemmas", "confusion"], case_sensitive=False), help="Which feature to evaluate.", default="tags")
# fmt: on
def evaluate_predictions(
    predictions,
    fields,
    morphlex_vocab,
    pretrained_vocab,
    train_tokens,
    train_lemmas,
    criteria,
    feature,
):
    """Evaluate predictions.

    Args:
        predictions: The tagged test file.
        fields: The fields present in the test file. Separated with ',', f.ex. 'tokens,gold_tags,tags'."""
    ds = FieldedDataset.from_file(predictions, fields=tuple(fields.split(",")))
    train_tokens = Vocab.from_file(train_tokens)
    if feature == "tags":
        morphlex_vocab = Vocab.from_file(morphlex_vocab)
        pretrained_vocab = Vocab.from_file(pretrained_vocab)
        evaluation = evaluate.TaggingEvaluation(
            ds,
            train_vocab=train_tokens,
            external_vocabs=evaluate.ExternalVocabularies(
                morphlex_vocab, pretrained_vocab
            ),
        )
        if criteria == "accuracy":
            click.echo(evaluate.format_result(evaluation._tagging_accuracy(ds)))
        elif criteria == "profile":
            click.echo(evaluate.format_profile(evaluation.tagging_profile(ds)))

    elif feature == "lemmas":
        train_lemmas = Vocab.from_file(train_lemmas)
        evaluation = evaluate.LemmatizationEvaluation(
            test_dataset=ds, train_vocab=train_tokens, train_lemmas=train_lemmas
        )
        if criteria == "accuracy":
            click.echo(evaluate.format_result(evaluation._lemma_accuracy(ds)))
        elif criteria == "profile":
            click.echo(evaluate.format_profile(evaluation.lemma_profile(ds)))
    elif feature == "confusion":
        train_lemmas = Vocab.from_file(train_lemmas)
        morphlex_vocab = Vocab.from_file(morphlex_vocab)
        pretrained_vocab = Vocab.from_file(pretrained_vocab)
        evaluation = evaluate.TaggingLemmatizationEvaluation(
            test_dataset=ds,
            train_vocab=train_tokens,
            external_vocabs=evaluate.ExternalVocabularies(
                morphlex_vocab, pretrained_vocab
            ),
            train_lemmas=train_lemmas,
        )
        click.echo(evaluation.lemma_tag_confusion_matrix())


# @cli.command()
# @click.argument("directory")
# @click.argument("fields")
# @click.option(
#     "--morphlex_vocab",
#     help="The location of the morphlex vocabulary.",
#     default=MORPHLEX_VOCAB_PATH,
# )
# @click.option(
#     "--pretrained_vocab",
#     help="The location of the pretrained vocabulary.",
#     default=PRETRAINED_VOCAB_PATH,
# )
# @click.option(
#     "--morphlex_vocab",
#     help="The location of the morphlex vocabulary.",
#     default=MORPHLEX_VOCAB_PATH,
# )
# @click.option(
#     "--criteria",
#     type=click.Choice(["accuracy", "profile"], case_sensitive=False),
#     help="Which criteria to evaluate.",
#     default="accuracy",
# )
# # fmt: on
# def evaluate_experiments(
#     directory, pretrained_vocab, morphlex_vocab, criteria, profile
# ):
#     """Evaluate the model predictions in the directory. If the directory contains other directories, it will recurse into it."""
#     experiments = evaluate.collect_experiments(
#         directory, morphlex_vocab, pretrained_vocab
#     )
#     click.echo(
#         evaluate.format_results(
#             evaluate.all_accuracy_average(experiments, type=criteria)
#         )
#     )
#     if profile:
#         error_profile = reduce(
#             add, [experiment.error_profile(type=criteria) for experiment in experiments]
#         )
#         click.echo(evaluate.format_profile(error_profile, up_to=60))
#         # Only possible if predictions contain both lemmas and tags
#         if "Fix me" == "Fix me":
#             confusion_matrix = reduce(
#                 add,
#                 [experiment.lemma_tag_confusion_matrix() for experiment in experiments],
#             )
#             click.echo(evaluate.format_profile(confusion_matrix, up_to=60))