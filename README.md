![Version](https://img.shields.io/badge/Version-3.1.0-darkviolet)
![Python](https://img.shields.io/badge/python-3.8|3.9-blue?logo=python&logoColor=white)
![CI Status](https://github.com/icelandic-lt/POS/actions/workflows/python-package.yml/badge.svg)
![Docker](https://img.shields.io/badge/Docker-green)

# POS tagger and lemmatizer for Icelandic
The goal of this project is to create a combined part-of-speech tagger and lemmatizer for Icelandic using the revised fine-grained tagging schema for Icelandic.
For further information about the schema see [MIM-Gold tagset](MIM_gold_tagset_2.0.pdf).

# Status

This project is a successor to the [ABLTagger](https://github.com/steinst/ABLTagger) by using modernized frameworks, some model modifications and Embedding model adaptations. However, the base principles are the same.
The accuracy of the models is however not better than with much simpler approaches used e.g. in [IceEval](https://github.com/icelandic-lt/IceEval), where a general Icelandic Language model is fine-tuned to the PoS tagging task. Therefore, it should be considered, using those approaches instead.

Please also note findings in the Paper [Is Part-of-Speech Tagging a Solved Problem for Icelandic?](https://aclanthology.org/2023.nodalida-1.8.pdf), for gaining more insights into the performance of these models.

# Description

This work is based on the ABLTagger (in [References](#references)) but with considerable model modifications and runs on Python 3.8, PyTorch 1.7.0,<2.0.0 and [transformers >=4.1.1,<=4.28.0](https://github.com/huggingface/transformers).

- [Versions](#versions)
- [Installation](#installation)
- [Running the models](#running-the-models)
  * [Note](#note)
  * [Command line usage](#command-line-usage)
  * [Python module](#python-module)
- [License](#license)
- [Authors](#authors)
  * [Acknowledgments](#acknowledgments)
- [Contributing](#contributing)
  * [Installation](#installation-1)
  * [Running the tests](#running-the-tests)
  * [Continuous integration](#continuous-integration)
  * [Training data](#training-data)
  * [Additional training data (Morphological lexicon)](#additional-training-data--morphological-lexicon-)
    + [Filtering the morphological lexicon](#filtering-the-morphological-lexicon)
  * [Training models](#training-models)
- [References](#references)

<small><i><a href='http://ecotrust-canada.github.io/markdown-toc/'>Table of contents generated with markdown-toc</a></i></small>

# Versions
See [releases](https://github.com/icelandic-lt/POS/releases)

# Installation
To use a pretrained model follow the instructions below.

```
# Using v3.1.0 - consider using the latest version: [releases](https://github.com/icelandic-lt/POS/releases)
pip install git+https://github.com/icelandic-lt/POS.git@v3.1.0
```
The models will be downloaded automatically when needed. The models are stored in `~/.cache/torch/hub`, for more information see [Torch hub documentation](https://pytorch.org/docs/stable/hub.html)

Instructions for further development can be found in [Contributing](#Contributing).

# Running the models
The models expect input to be tokenized and a tokenizer is not bundled with this package. We reccomend [tokenizer](https://github.com/mideind/Tokenizer) version 2.0+.

There are three pretrained models available.
- A small PoS tagger: `pos tag example.txt tagged.txt`
- A large PoS tagger: `pos tag-large example.txt tagged.txt`
- A small lemmatzier : `pos lemma example.txt tagged.txt`

Below is a table with some rough numbers (they are dependant on hardware and text domain).

|           | Accuracy (MIM-Gold) | Disk space | CPU speed | GPU speed |
|-----------|---------------------|------------|-----------|-----------|
| PoS small | ~96.7%              | ~60MB      | 360       | 10000     |
| PoS large | ~97.8%              | ~425MB     | 20        | 1100      |
| Lemmatizer small | ~98.3%              | ~72MB      | 360       | 10000     |

## Note
- The models are currently not trained on "noisy" text, thus they might not preform as well on text which is far from the data in MIM-Gold.
- The `batch_size` parameter works best with GPUs.
- The accuracy of the lemmatizer is acceptable on MIM-GOLD but it does not generalize well and errors returned by the model are sometimes hard to accept. We rather recommend using Nefnir as a main lemmatizer with a fallback to the neural Lemmatizer.

## Command line usage
Note that the input and output should be paths (i.e. not stdin or stdout).

`example.txt` is correctly formatted input file: One token per line and sentences are separated with an empty line.
```Bash
cat example.txt 
Þar
sem
jökulinn
ber
við
loft
hættir
landið
að
vera
jarðneskt
,
en
jörðin
fær
hlutdeild
í
himninum
,
þar
búa
ekki
framar
neinar
sorgir
og
þess
vegna
er
gleðin
ekki
nauðsynleg
,
þar
ríkir
fegurðin
ein
, 
ofar
hverri
kröfu
.

Halldór
Laxness
```
Tagging this file
```Bash
pos tag-large example.txt example_tagged.txt
...
cat example_tagged.txt 
Þar     aa
sem     c
jökulinn        nkeog
ber     sfg3en
við     af
loft    nheo
hættir  sfg3en
landið  nheng
að      cn
vera    sng
jarðneskt       lhensf
,       pk
en      c
jörðin  nveng
fær     sfg3en
hlutdeild       nveo
í       af
himninum        nkeþg
,       pk
þar     aa
búa     sfg3fn
ekki    aa
framar  aam
neinar  fovfn
sorgir  nvfn
og      c
þess    fphee
vegna   af
er      sfg3en
gleðin  nveng
ekki    aa
nauðsynleg      lvensf
,       pk
þar     aa
ríkir   sfg3en
fegurðin        nveng
ein     lvensf
,       pk
ofar    afm
hverri  foveþ
kröfu   nveþ
.       pl

Halldór nken-s
Laxness nken-s
```

And then adding the lemmas:
```
pos lemma example_tagged.txt  # If you have previously been using and older version of the PoS tagger and this fails. Try adding the "--force_reload" flag to this command (once).
...
Þar	aa	þar
sem	c	sem
jökulinn	nkeog	jökull
ber	sfg3en	bera
við	af	við
loft	nheo	loft
hættir	sfg3en	hætta
landið	nheng	land
að	cn	að
vera	sng	vera
jarðneskt	lhensf	jarðneskur
,	pk	,
en	c	en
jörðin	nveng	jörð
fær	sfg3en	fá
hlutdeild	nveo	ílutdeild
í	af	í
himninum	nkeþg	himinn
,	pk	,
þar	aa	þar
búa	sfg3fn	búa
ekki	aa	ekki
framar	aam	framar
neinar	fovfn	neinn
sorgir	nvfn	sorg
og	c	og
þess	fphee	það
vegna	af	vegna
er	sfg3en	vera
gleðin	nveng	gleði
ekki	aa	ekki
nauðsynleg	lvensf	nauðsynlegur
,	pk	,
þar	aa	þar
ríkir	sfg3en	ríkja
fegurðin	nveng	regurð
ein	lvensf	einn
,	pk	,
ofar	afm	ofar
hverri	foveþ	hver
kröfu	nveþ	krafa
.	pl	.

Halldór	nken-s	Ialldór
Laxness	nken-s	Laxness

```
For additional flags and further details see `pos tag --help`

## Python module
Usage example of the tagger in another Python module [example.py](example.py).
```Python
"""An example of the POS tagger as a module."""
import torch

import pos

# Initialize the tagger
device = torch.device("cpu")  # CPU
tagger: pos.Tagger = torch.hub.load(
    repo_or_dir="icelandic-lt/POS",
    model="tag", # This specifies which model to use. Set to 'tag_large' for large model.
    device=device,
    force_reload=False,
    force_download=False,
)

# Tag a single sentence
tags = tagger.tag_sent(("Þetta", "er", "setning", "."))
print(tags)
# ('fahen', 'sfg3en', 'nven', 'pl')
# Tuple[str, ...]

# Tag multiple sentences at the same time (faster).
tags = tagger.tag_bulk(
    (("Þetta", "er", "setning", "."), ("Og", "önnur", "!")), batch_size=2
)  # Batch size works best with GPUs
print(tags)
# (('fahen', 'sfg3en', 'nven', 'pl'), ('c', 'foven', 'pl'))
# Tuple[Tuple[str, ...], ...]

# Tag a correctly formatted file.
dataset = pos.FieldedDataset.from_file("example.txt")
tags = tagger.tag_bulk(dataset)
print(tags)
# (('aa', 'ct', 'nkeog', 'sfg3en', 'af', 'nheo', 'sfg3en', 'nheng', 'cn', 'sng', 'lhensf', 'pk', 'c', 'nveng', 'sfg3en', 'nveo', 'af', 'nkeþg', 'pk', 'aa', 'sfg3fn', 'aa', 'aam', 'fovfn', 'nvfn', 'c', 'fphee', 'af', 'sfg3en', 'nveng', 'aa', 'lvensf', 'pk', 'aa', 'sfg3en', 'nveng', 'lvensf', 'pk', 'afm', 'foveþ', 'nveþ', 'pl'), ('nken-s', 'nken-s'))
# Tuple[Tuple[str, ...], ...]
```
For additional information, see the docstrings provided.

# License
[Apache v2.0](LICENSE)

# Authors
<a href="https://github.com/icelandic-lt/POS/graphs/contributors">
  <img src="https://contributors-img.web.app/image?repo=icelandic-lt/POS" />
</a>
<!-- Made with [contributors-img](https://contributors-img.web.app). -->

- Haukur Páll Jónsson
- Örvar Kárason
- Steinþór Steingrímsson

## Acknowledgments
- Reykjavík University

This project was funded (partly) by the Language Technology Programme for Icelandic 2019-2023. The programme, which is managed and coordinated by [Almannarómur](https://almannaromur.is/), is funded by the Icelandic Ministry of Education, Science and Culture.

# Contributing
For more involved installation instructions and how to train different models.

## Installation
We use [poetry](https://python-poetry.org/) to manage dependencies and to build wheels. Install poetry and do `poetry install`.
To activate the environment within the current shell call `poetry shell`.

## Running the tests
To run the tests simply run `pytest` within the `poetry` environment.
To run without starting the environment, run `poetry run pytest`.

This will run all the unit-tests and skip a few tests which rely on external data (model files).

To include these tests make sure to add additional options to the `pytest` command.
- `pytest --electra_model="electra_model/"` a directory containing all necessary files to load an electra model.
- `pytest --tagger="tagger.pt" --dictionaries="dictionaries.pickle"`, the necessary files to load a pretrained tagging model.

## Continuous integration
This project uses GitHub actions to run a number of checks (linting, testing) when a change is pushed to GitHub.
If a change does not pass the checks, a code fix is expected.
See `.github/workflows/python-package.yml` for the checks involved.

## Training data
The training data is a text file wich contains PoS-tagged sentences. The file has one token per line, as well as its corresponding tag. The sentences are separated by an empty line. 

```
Við     fp1fn
höfum   sfg1fn
góða    lveosf
aðstöðu nveo
fyrir   af
barnavagna      nkfo
og      c
kerrur  nvfo
.       pl

Börnin  nhfng
geta    sfg3fn
sofið   sþghen
úti     aa
ef      c
vill    sfg3en
.       pl
```

For Icelandic we used the [IFD](https://repository.clarin.is/repository/xmlui/handle/20.500.12537/38) and [MIM-GOLD](https://repository.clarin.is/repository/xmlui/handle/20.500.12537/40) (*Ice*. OTB).
We use the 10th fold (in either dataset) for hyperparameter selection.

We provide some additional data which is used to train the model:
- `data/extra/characters_training.txt` contains all the characters which the model knows.
Unknown characters are mapped to `<unk>`

## Additional training data (Morphological lexicon)
We represent the information contained in the morphological lexicon with n-hot vectors.
To generate the n-hot vectors, different scripts will have to be written for different morphological lexicons.
We use the DMII morphological lexicon for Icelandic.
The script, `pos/vectorize_dim.py` is used to create n-hot vectors from DMII.
We first [download the data in SHsnid format](https://bin.arnastofnun.is/django/api/nidurhal/?file=SHsnid.csv.zip).
After unpacking the `SHsnid.csv` to `./data/extra`.
To generate the n-hot vectors we run the script:
```
python3 ./pos/vectorize_dim.py 
```
The script takes two parameters:
| Parameters                 | Default       | Description   |	
| :------------------------ |:-------------:| :-------------|
| -i --input 	       |	./data/extra/SHsnid.csv           |The file containing the DIM morphological lexicon in SHsnid format.
| -o  --output          | ./data/extra/dmii.vectors           |The file containing the DIM n-hot vectors.

### Filtering the morphological lexicon
Since the morphological lexicon contains more words than will be seen during training and testing, it is useful to filter out unseen words.

```
pos filter-embedding data/raw/mim/* data/raw/otb/* data/extra/dmii.vectors data/extra/dmii.vectors_filtered bin
```
For an explanation of the parameters run `pos filter-embedding --help`

## Training models
A model can be trained by invoking the following command.
```
pos train-and-tag \
  training_data/*.tsv \
  testing_data.tsv \
  out # A directory to write out training results
```
For a description of all the arguments and options, run `pos train-and-tag --help`.

Parameters with default values (options) are prefixed with `--`.

It is also useful to look at the BASH scripts in `bin/`

# References
[Augmenting a BiLSTM Tagger with a Morphological Lexicon and a Lexical Category Identification Step](https://www.aclweb.org/anthology/R19-1133/)
```
@inproceedings{steingrimsson-etal-2019-augmenting,
    title = "Augmenting a {B}i{LSTM} Tagger with a Morphological Lexicon and a Lexical Category Identification Step",
    author = {Steingr{\'\i}msson, Stein{\th}{\'o}r  and
      K{\'a}rason, {\"O}rvar  and
      Loftsson, Hrafn},
    booktitle = "Proceedings of the International Conference on Recent Advances in Natural Language Processing (RANLP 2019)",
    month = sep,
    year = "2019",
    address = "Varna, Bulgaria",
    url = "https://www.aclweb.org/anthology/R19-1133",
    doi = "10.26615/978-954-452-056-4_133",
    pages = "1161--1168",
}
```
