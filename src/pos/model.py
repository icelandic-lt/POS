"""The tagging Module."""
from enum import Enum
import logging
from typing import List, Mapping, Sequence, Tuple, Any, Dict, Optional, cast
import abc
import random

from flair.embeddings import TransformerWordEmbeddings
from flair.data import Sentence as f_sentence
import torch
from torch import Tensor, stack
from torch.nn.utils.rnn import pack_padded_sequence, pad_packed_sequence, pad_sequence
import torch.nn as nn

from . import core
from .core import Sentence, Sentences, VocabMap
from .data import (
    copy_into_larger_tensor,
    map_to_index,
    map_to_chars_batch,
    BATCH_KEYS,
    map_to_index_batch,
)


log = logging.getLogger(__name__)


class Modules(Enum):
    """To hold the module names."""

    Pretrained = "pretrained"
    Trained = "trained"
    MorphLex = "morphlex"
    CharactersToTokens = "chars"
    BiLSTM = "bilstm"
    BERT = "bert"
    Tagger = "tagger"
    Lemmatizer = "lemmatizer"


class BatchPostprocess(metaclass=abc.ABCMeta):
    """An interface to handle postprocessing for modules."""

    @abc.abstractmethod
    def postprocess(self, batch: Tensor, lengths: Sequence[int]) -> Sentences:
        """Postprocess the model output."""
        raise NotImplementedError


class BatchPreprocess(metaclass=abc.ABCMeta):
    """An interface to handle preprocessing for modules."""

    @abc.abstractmethod
    def preprocess(self, batch: Sequence[Sentence]) -> Tensor:
        """Preprocess the sentence batch."""
        raise NotImplementedError


class Embedding(BatchPreprocess, nn.Module, metaclass=abc.ABCMeta):
    """A module which accepts string inputs and embeds them to tensors."""

    def forward(self, batch: Sequence[Sentence], lengths: Sequence[int]) -> Tensor:
        """Run a generic forward pass for the Embeddings."""
        return self.embed(self.preprocess(batch), lengths).to(core.device)

    @abc.abstractmethod
    def embed(self, batch: Tensor, lengths: Sequence[int]) -> Tensor:
        """Apply the embedding."""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def output_dim(self) -> int:
        """Return the output dimension."""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def to_bilstm(self) -> bool:
        """Return True if output should be added to BiLSTM."""
        raise NotImplementedError


class ClassingWordEmbedding(Embedding):
    """Classic word embeddings."""

    def __init__(
        self,
        vocab_map: VocabMap,
        embedding_dim: int,
        padding_idx=0,
        pass_to_bilstm=True,
    ):
        """Create one."""
        super().__init__()
        self.vocab_map = vocab_map
        self.pass_to_bilstm = pass_to_bilstm
        self.embedding = nn.Embedding(
            len(vocab_map), embedding_dim, padding_idx=padding_idx
        )
        # Skip the first index, should be zero
        nn.init.xavier_uniform_(self.embedding.weight[1:, :])

    def preprocess(self, batch: Sequence[Sentence]) -> Tensor:
        """Preprocess the sentence batch."""
        return pad_sequence(
            [map_to_index(x, w2i=self.vocab_map.w2i) for x in batch],
            batch_first=True,
        )

    def embed(self, batch: Tensor, lengths: Sequence[int]) -> Tensor:
        """Apply the embedding."""
        return self.embedding(batch)

    @property
    def to_bilstm(self):
        """Return True if output should be added to BiLSTM."""
        return self.pass_to_bilstm

    @property
    def output_dim(self):
        """Return the output dimension."""
        return self.embedding.weight.data.shape[1]


class PretrainedEmbedding(ClassingWordEmbedding):
    """The Morphological Lexicion embeddings."""

    def __init__(
        self,
        vocab_map: VocabMap,
        embeddings: Tensor,
        freeze=False,
        padding_idx=0,
        pass_to_bilstm=True,
    ):
        """Create one."""
        super().__init__(
            vocab_map=vocab_map,
            embedding_dim=1,
            padding_idx=padding_idx,
            pass_to_bilstm=pass_to_bilstm,
        )  # we overwrite the embedding
        self.embedding = nn.Embedding.from_pretrained(
            embeddings,
            freeze=freeze,
            padding_idx=padding_idx,
        )


class CharacterAsWordEmbedding(Embedding):
    """A Character as Word Embedding."""

    def __init__(
        self,
        vocab_map: VocabMap,
        character_embedding_dim=20,
        char_lstm_dim=64,
        char_lstm_layers=1,
        padding_idx=0,
        pass_to_bilstm=True,
    ):
        """Create one."""
        super().__init__()
        self.vocab_map = vocab_map
        self.pass_to_bilstm = pass_to_bilstm
        self.character_embedding = nn.Embedding(
            len(vocab_map), character_embedding_dim, padding_idx=padding_idx
        )
        nn.init.xavier_uniform_(self.character_embedding.weight[1:, :])
        # The character BiLSTM
        self.char_bilstm = nn.LSTM(
            input_size=character_embedding_dim,
            hidden_size=char_lstm_dim,
            num_layers=char_lstm_layers,
            batch_first=True,
            bidirectional=True,
        )
        for name, param in self.char_bilstm.named_parameters():
            if "bias" in name:
                nn.init.constant_(param, 0.0)
            else:
                nn.init.xavier_uniform_(param)
        self._output_dim = 2 * char_lstm_dim

    def preprocess(self, batch: Sequence[Sentence]) -> Tensor:
        """Preprocess the sentence batch."""
        return map_to_chars_batch(batch, self.vocab_map.w2i)

    def embed(self, batch: Tensor, lengths: Sequence[int]) -> Tensor:
        """Apply the embedding."""
        # (b * seq, chars)
        char_embs = self.character_embedding(batch)
        # (b * seq, chars, f)
        self.char_bilstm.flatten_parameters()
        out, _ = self.char_bilstm(char_embs)
        last_timestep = out[:, -1, :]  # Only the last timestep
        return last_timestep.reshape(
            len(lengths), -1, out.shape[-1]
        )  # Map to (batch, tokens, features)

    @property
    def to_bilstm(self):
        """Return True if output should be added to BiLSTM."""
        return self.pass_to_bilstm

    @property
    def output_dim(self):
        """Return the output dimension."""
        return self._output_dim


class FlairTransformerEmbedding(Embedding):
    """A wrapper for the TransformerEmbedding from Flair. It's here to fit into the preprocessing setup."""

    def __init__(self, file_path, pass_to_bilstm=False, **kwargs):
        """Initialize the embeddings."""
        super().__init__()
        self.pass_to_bilstm = pass_to_bilstm
        self.emb = TransformerWordEmbeddings(
            file_path,
            layers=kwargs.get("transformer_layers", "-1"),
            use_scalar_mix=kwargs.get("transformer_use_scalar_mix", False),
            allow_long_sentences=kwargs.get("transformer_allow_long_sentences", True),
            fine_tune=True,
            batch_size=kwargs.get("batch_size", 1),
        )
        self._output_dim = kwargs.get("bert_encoder_dim", 256)

    def preprocess(self, batch: Sequence[Sentence]) -> Tensor:
        """Preprocess the sentence batch."""
        f_sentences = [f_sentence(" ".join(sentence)) for sentence in batch]
        self.emb.embed(f_sentences)
        return pad_sequence(
            [
                stack(tuple(token.embedding for token in sentence))
                for sentence in f_sentences
            ],
            batch_first=True,
        )

    def embed(self, batch: Tensor, lengths: Sequence[int]) -> Tensor:
        """Apply the embedding."""
        return batch

    @property
    def to_bilstm(self):
        """Return True if output should be added to BiLSTM."""
        return self.pass_to_bilstm

    @property
    def output_dim(self):
        """Return the output dimension."""
        return self._output_dim


class Decoder(BatchPostprocess, nn.Module, metaclass=abc.ABCMeta):
    """A module which accepts an sentence embedding and outputs another tensor."""

    @property
    @abc.abstractmethod
    def output_dim(self) -> int:
        """Return the output dimension."""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def weight(self) -> int:
        """Return the decoder weight."""
        raise NotImplementedError

    @abc.abstractmethod
    def add_targets(self, batch: Dict[BATCH_KEYS, Any]):
        """Add the decoder targets to the batch dictionary. SIDE-EFFECTS!."""

    @abc.abstractmethod
    def decode(self, encoded: Tensor, batch: Dict[BATCH_KEYS, Any]) -> Tensor:
        """Run the decoder on the batch."""

    def forward(self, encoded: Tensor, batch: Dict[BATCH_KEYS, Any]) -> Tensor:
        """Run a generic forward pass for the Embeddings."""
        self.add_targets(batch)
        return self.decode(encoded=encoded, batch=batch)


class GRUDecoder(Decoder):
    """Cho et al., 2014 GRU RNN decoder which uses the context vector for each timestep.

    Code adjusted from: https://github.com/bentrevett/pytorch-seq2seq/
    """

    def __init__(
        self,
        vocab_map: VocabMap,
        hidden_dim,
        emb_dim,
        teacher_forcing=0.0,
        dropout=0.0,
        weight=1,
    ):
        """Initialize the model."""
        super().__init__()
        self.vocab_map = vocab_map
        self.teacher_forcing = (
            teacher_forcing  # if rand < teacher_forcing we will use teacher forcing.
        )
        self.hidden_dim = hidden_dim  # The internal dimension of the GRU model
        self._output_dim = len(
            vocab_map
        )  # The number of characters, these will be interpreted as logits.
        self._weight = weight

        self.embedding = nn.Embedding(
            len(vocab_map), emb_dim
        )  # We map the input idx to vectors.
        self.rnn = nn.GRU(
            emb_dim + hidden_dim, hidden_dim, batch_first=True
        )  # The input is the embedding and context

        self.fc_out = nn.Linear(
            emb_dim + hidden_dim + hidden_dim, len(vocab_map)
        )  # Map to logits.
        self.illegal_chars_output = {
            self.vocab_map.SOS_ID,
            self.vocab_map.PAD_ID,
            self.vocab_map.UNK_ID,
        }
        self.dropout = nn.Dropout(dropout)  # Embedding dropout

    @property
    def output_dim(self) -> int:
        """Return the output dimension."""
        return self._output_dim

    @property
    def weight(self) -> int:
        """Return the decoder weight."""
        return self._weight

    def map_lemma_from_char_idx(self, char_idxs: List[int]) -> str:
        """Map a lemma from character indices."""
        chars = [
            self.vocab_map.i2w[char_idx]
            for char_idx in char_idxs
            if char_idx not in self.illegal_chars_output
        ]
        # If we find an EOS, we cut from there.
        if self.vocab_map.EOS in chars:
            eos_idx = chars.index(self.vocab_map.EOS)
            chars = chars[:eos_idx]
        return "".join(chars)

    def map_sentence_chars(
        self, sent: List[List[int]], sent_length: int
    ) -> Tuple[str, ...]:
        """Map a sentence characters from idx to strings and join to lemmas."""
        lemmas: List[str] = []
        for tok_num in range(sent_length):
            lemmas.append(self.map_lemma_from_char_idx(sent[tok_num]))
        return tuple(lemmas)

    def postprocess(self, batch: Tensor, lengths: Sequence[int]) -> Sentences:
        """Postprocess the model output."""
        # Get the character predictions
        char_preds = batch.argmax(dim=2)
        # Map to batch of sentences again.
        sent_char_preds = char_preds.view(size=(len(lengths), -1, char_preds.shape[-1]))
        as_list = sent_char_preds.tolist()

        sentence_lemmas = []
        for sent, sent_length in zip(as_list, lengths):
            sentence_lemmas.append(self.map_sentence_chars(sent, sent_length))
        return tuple(sentence_lemmas)

    def add_targets(self, batch: Dict[BATCH_KEYS, Any]):
        """Preprocess the sentence batch. HAS SIDE-EFFECTS!."""
        if BATCH_KEYS.LEMMAS in batch:
            batch[BATCH_KEYS.TARGET_LEMMAS] = map_to_chars_batch(
                batch[BATCH_KEYS.LEMMAS], self.vocab_map.w2i, add_sos=False
            )

    @staticmethod
    def _get_char_input_next_timestep(
        vocab_map: VocabMap,
        batch: Dict[BATCH_KEYS, Any],
        previous_predictions: Optional[Tensor] = None,
        teacher_forcing=0.5,
    ):
        """Get the next character timestep to feed the model."""
        if previous_predictions is None:  # First timestep
            # We assume that tokens and lemmas work equally well for the first timestep (PAD or SOS)
            return map_to_chars_batch(batch[BATCH_KEYS.TOKENS], vocab_map.w2i)[:, 0]
        previous_timestep = previous_predictions.shape[1] - 1  # -1 for 0 indexing
        # We have the targets and teacher forcing
        if BATCH_KEYS.TARGET_LEMMAS in batch and random.random() < teacher_forcing:
            return batch[BATCH_KEYS.TARGET_LEMMAS][:, previous_timestep]
        # We don't have targets or no teacher forcing
        return previous_predictions[:, previous_timestep, :].argmax(dim=1)

    def decode(self, encoded: Tensor, batch: Dict[BATCH_KEYS, Any]) -> Tensor:
        """Run the decoder on the batch."""
        # [batch_size * max_seq_len, emb_size]
        context = encoded.reshape(shape=(-1, encoded.shape[2]))

        # [batch_size * max_seq_len, max_token_len, emb_size]
        predictions: Optional[Tensor] = None

        hidden = context.reshape((1, context.shape[0], context.shape[1]))
        # return torch.zeros(size=(1, batch_size, self.hidden_dim)).to(core.device)
        # self.initial_hidden(context.shape[0])
        # We are training
        if BATCH_KEYS.TARGET_LEMMAS in batch:
            for _ in range(
                batch[BATCH_KEYS.TARGET_LEMMAS].shape[1]
            ):  # Iterate over characters
                next_char_input = self._get_char_input_next_timestep(
                    self.vocab_map,
                    batch,
                    predictions,
                    teacher_forcing=self.teacher_forcing,
                ).to(core.device)
                emb_chars = self.dropout(self.embedding(next_char_input))
                gru_in = torch.cat((emb_chars, context), dim=1).unsqueeze(
                    1
                )  # Add the time-step
                output, hidden = self.rnn(gru_in, hidden)
                prediction = self.fc_out(torch.cat((gru_in, output), dim=2))
                if predictions is None:
                    predictions = prediction
                else:
                    predictions = torch.cat((predictions, prediction), dim=1)

        # We decode
        # TODO: support decoding until EOS/PAD for all
        else:
            pass
        predictions = cast(Tensor, predictions)
        return predictions


class Tagger(Decoder):
    """A tagger; accept some tensor input and return logits over classes."""

    def __init__(self, vocab_map: VocabMap, input_dim, weight=1):
        """Initialize."""
        super().__init__()
        self.vocab_map = vocab_map
        output_dim = len(vocab_map)
        self.tagger = nn.Linear(input_dim, output_dim)
        nn.init.xavier_uniform_(self.tagger.weight)
        self._output_dim = output_dim
        self._weight = weight

    @property
    def output_dim(self) -> int:
        """Return the output dimension."""
        return self._output_dim

    @property
    def weight(self) -> int:
        """Return the decoder weight."""
        return self._weight

    def decode(self, encoded: Tensor, batch: Dict[BATCH_KEYS, Any]) -> Tensor:
        """Run the decoder on the batch."""
        return self.tagger(encoded)

    def add_targets(self, batch: Dict[BATCH_KEYS, Any]):
        """Add the decoder targets to the batch dictionary. SIDE-EFFECTS!."""
        if BATCH_KEYS.FULL_TAGS in batch:
            batch[BATCH_KEYS.TARGET_FULL_TAGS] = map_to_index_batch(
                batch[BATCH_KEYS.FULL_TAGS], self.vocab_map.w2i
            )

    def postprocess(self, batch: Tensor, lengths: Sequence[int]) -> Sentences:
        """Postprocess the model output."""
        idxs = batch.argmax(dim=2).tolist()

        tags = [
            tuple(
                self.vocab_map.i2w[tag_idx]
                for token_count, tag_idx in enumerate(sent)
                # All sentences are padded (at the right end) to be of equal length.
                # We do not want to return tags for the paddings.
                # We check the information about lengths and paddings.
                if token_count < lengths[sent_idx]
            )
            for sent_idx, sent in enumerate(idxs)
        ]
        return tuple(tags)


class Encoder(nn.Module):
    """The Pytorch module implementing the encoder."""

    def __init__(
        self,
        embeddings: Dict[Modules, Embedding],
        main_lstm_dim=64,  # The main LSTM dim will output with this dim
        main_lstm_layers=0,  # The main LSTM layers
        lstm_dropouts=0.0,
        input_dropouts=0.0,
        noise=0.1,
        **kwargs
    ):
        """Initialize the module given the parameters."""
        super().__init__()
        self.noise = noise
        self.embeddings = nn.ModuleDict(
            {key.value: emb for key, emb in embeddings.items()}
        )

        self.use_bilstm = not main_lstm_layers == 0
        encoder_out_dim = sum(
            emb.output_dim for emb in self.embeddings.values() if not emb.to_bilstm
        )
        bilstm_in_dim = sum(
            emb.output_dim for emb in self.embeddings.values() if emb.to_bilstm
        )
        if bilstm_in_dim and not self.use_bilstm:
            raise ValueError("Not using BiLSTM but Embedding is set to use BiLSTM")

        # BiLSTM over all inputs
        if self.use_bilstm:
            self.bilstm = nn.LSTM(
                input_size=bilstm_in_dim,
                hidden_size=main_lstm_dim,
                num_layers=main_lstm_layers,
                dropout=lstm_dropouts,
                batch_first=True,
                bidirectional=True,
            )
            for name, param in self.bilstm.named_parameters():
                if "bias" in name:
                    nn.init.constant_(param, 0.0)
                elif "weight" in name:
                    nn.init.xavier_uniform_(param)
                else:
                    raise ValueError("Unknown parameter in lstm={name}")
            encoder_out_dim += main_lstm_dim * 2
        self.main_bilstm_out_dropout = nn.Dropout(p=input_dropouts)
        self.output_dim = encoder_out_dim

    def forward(self, batch: Sequence[Sentence], lengths: Sequence[int]):
        """Run a forward pass through the module. Input should be tensors."""
        # input is (batch_size=num_sentence, max_seq_len_in_batch=max(len(sentences)), max_word_len_in_batch + 1 + 1)
        # Embeddings
        list_to_bilstm = [
            emb(batch, lengths) for emb in self.embeddings.values() if emb.to_bilstm
        ]
        embs_to_bilstm = None
        if list_to_bilstm:
            embs_to_bilstm = torch.cat(list_to_bilstm, dim=2)
        list_embs = [
            emb(batch, lengths) for emb in self.embeddings.values() if not emb.to_bilstm
        ]
        embs = None
        if list_embs:
            embs = torch.cat(list_embs, dim=2)

        # Add noise - like in dyney
        # if self.training and main_in is not None:
        #     main_in = main_in + torch.empty_like(main_in).normal_(0, self.noise)
        # (b, seq, f)

        if self.use_bilstm and embs_to_bilstm is not None:

            # Pack the paddings
            packed = pack_padded_sequence(
                embs_to_bilstm,
                lengths,
                batch_first=True,
                enforce_sorted=False,
            )
            # Make sure that the parameters are contiguous.
            self.bilstm.flatten_parameters()
            # Ignore the hidden outputs
            packed_out, _ = self.bilstm(packed)
            # Unpack and ignore the lengths
            bilstm_out, _ = pad_packed_sequence(packed_out, batch_first=True)
            bilstm_out = self.main_bilstm_out_dropout(bilstm_out)
            if embs:
                embs = torch.cat([embs, bilstm_out], dim=2)
            else:
                embs = bilstm_out

        return embs


class ABLTagger(nn.Module):
    """The ABLTagger, consists of an Encoder(multipart) and a Tagger."""

    def __init__(self, encoder: Encoder, decoders: Dict[Modules, Decoder]):
        """Initialize the tagger."""
        super().__init__()
        self.encoder = encoder
        self.decoders = nn.ModuleDict({key.value: emb for key, emb in decoders.items()})
        self.decoders = cast(Mapping[str, Decoder], self.decoders)

    def forward(self, batch: Dict[BATCH_KEYS, Any]) -> Dict[Modules, Tensor]:
        """Forward pass."""
        encoded = self.encoder(batch[BATCH_KEYS.TOKENS], batch[BATCH_KEYS.LENGTHS])
        return {
            Modules(key): decoder(encoded, batch)
            for key, decoder in self.decoders.items()
        }


def pack_sequence(padded_sequence):
    """Pack the PAD in a sequence. Assumes that PAD=0.0 and appended."""
    # input:
    # (b, s, f)
    # lengths = (b, s)
    lengths = torch.sum(torch.pow(padded_sequence, 2), dim=2)
    # lengths = (b)
    lengths = torch.sum(
        lengths != torch.Tensor([0.0]).to(padded_sequence.device),
        dim=1,
    )
    return (
        pack_padded_sequence(
            padded_sequence, lengths, batch_first=True, enforce_sorted=False
        ),
        lengths,
    )


def unpack_sequence(packed_sequence):
    """Inverse of pack_sequence."""
    return pad_packed_sequence(packed_sequence, batch_first=True)[0]
