"""
HappyBERT
"""

# disable pylint TODO warning
# pylint: disable=W0511
from transformers import (
    BertForMaskedLM,
    BertForNextSentencePrediction,
    BertForQuestionAnswering,
    BertTokenizer
)

import torch

from happy_transformer.happy_transformer import HappyTransformer


class HappyBERT(HappyTransformer):
    """
    A wrapper over PyTorch's BERT transformer implementation
    """

    def __init__(self, model='bert-large-uncased'):
        super().__init__(model)
        self.mlm = None  # Masked Language Model
        self.nsp = None  # Next Sentence Prediction
        self.qa = None   # Question Answering
        self.tokenizer = BertTokenizer.from_pretrained(model)
        self.masked_token = self.tokenizer.mask_token
        self.sep_token = self.tokenizer.sep_token
        self.cls_token = self.tokenizer.cls_token
        self.model = 'BERT'

    def _get_masked_language_model(self):
        """
        Initializes the BertForMaskedLM transformer
        """
        self.mlm = BertForMaskedLM.from_pretrained(self.model_to_use)
        self.mlm.eval()

    def _get_next_sentence_prediction(self):
        """
        Initializes the BertForNextSentencePrediction transformer
        """
        self.nsp = BertForNextSentencePrediction.from_pretrained(self.model_to_use)
        self.nsp.eval()

    def _get_question_answering(self):
        """
        Initializes the BertForQuestionAnswering transformer
        NOTE: This uses the bert-large-uncased-whole-word-masking-finetuned-squad pretraining for best results.
        """
        self.qa = BertForQuestionAnswering.from_pretrained('bert-large-uncased-whole-word-masking-finetuned-squad')
        self.qa.eval()

    def _get_prediction_softmax(self, text: str):
        """
        BERT's "_get_prediction_softmax" is the default in HappyTransformer
        """
        return self._get_prediction_softmax(text)

    def is_next_sentence(self, a, b):
        """
        Determines if sentence B is likely to be a continuation after sentence
        A.
        :param a: First sentence
        :param b: Second sentence to test if it comes after the first
        :return tuple: True if b is likely to follow a, False if b is unlikely
                       to follow a, with the probabilities as the second item
                       of the tuple
        """
        if self.nsp is None:
            self._get_next_sentence_prediction()
        connected = a + ' ' + b
        tokenized_text = self._get_tokenized_text(connected)
        indexed_tokens = self.tokenizer.convert_tokens_to_ids(tokenized_text)
        segments_ids = self._get_segment_ids(tokenized_text)
        # Convert inputs to PyTorch tensors
        tokens_tensor = torch.tensor([indexed_tokens])
        segments_tensors = torch.tensor([segments_ids])
        with torch.no_grad():
            predictions = self.nsp(tokens_tensor, token_type_ids=segments_tensors)[0]
        print(type(predictions))
        softmax = self._softmax(predictions)
        if torch.argmax(softmax) == 0:
            return (True, softmax)
        else:
            return (False, softmax)

    def answer_question(self, question, context):
        """
        Using the given context returns the answer to the given question.

        :param question:
        :param context:
        """
        if self.qa is None:
            self._get_question_answering()
        input_text = "[CLS] " + question + " [SEP] " + context + " [SEP]"
        input_ids = self.tokenizer.encode(input_text)
        token_type_ids = [0 if i <= input_ids.index(102) else 1
                          for i in range(len(input_ids))]
        token_tensor = torch.tensor([input_ids])
        segment_tensor = torch.tensor([token_type_ids])
        with torch.no_grad():
            start_scores, end_scores = self.qa(token_tensor,
                                               token_type_ids=segment_tensor)
        all_tokens = self.tokenizer.convert_ids_to_tokens(input_ids)
        answer_list = all_tokens[torch.argmax(start_scores):
                                 torch.argmax(end_scores)+1]
        answer = self.tokenizer.convert_tokens_to_string(answer_list)
        answer = answer.replace(' \' ', '\' ').replace('\' s ', '\'s ')
        return answer