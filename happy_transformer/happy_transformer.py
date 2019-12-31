# disable pylint TODO warning
# pylint: disable=W0511
# pylint: disable=C0301



"""
HappyTransformer is a wrapper over pytorch_transformers to make it
easier to use.
"""

import string
import re
import numpy as np
import torch
import pandas as pd
import logging
import csv

import sys
import os


from happy_transformer.classifier_args import classifier_args
from happy_transformer.sequence_classification import SequenceClassifier


class HappyTransformer:
    """
    Initializes pytroch's transformer models and provided methods for
    their basic functionality.
    Philosophy: Automatically make decisions for the user so that they don't
                have to have any understanding of PyTorch or transformer
                models to be able to utilize their capabilities.
    """

    def __init__(self, model):
        # Transformer and tokenizer set in child class
        self.mlm = None  # Masked Language Model
        self.nsp = None  # Next Sentence Prediction
        self.seq = None # Sequence Classification
        self.qa = None   # Question Answering


        self.model_to_use = model
        self.tokenizer = None
        # Child class sets to indicate which model is being used
        self.model = ''
        self.tag_one_transformers = ['BERT', "ROBERTA", 'XLNET']

        # GPU support
        self.gpu_support = torch.device("cuda" if torch.cuda.is_available()
                                        else "cpu")
        print("Using model:", self.gpu_support)
        self.model_version = model
        self.seq_trained = False
        self.seq_args = None

        #logging
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)

    def _get_masked_language_model(self):
        # Must be overloaded
        # TODO make an exception to be thrown if not overloaded
        pass

    def _get_question_answering(self):
        # Must be overloaded
        # TODO make an exception to be thrown if not overloaded
        pass

    def predict_mask(self, text: str, options=None, k=1):
        """
        Method to predict what the masked token in the given text string is.
        NOTE: This is the generic version of this predict_mask method. If a
        child class needs a different implementation they should overload this
        method, not create a new method.
        :param text: a string with a masked token within it
        :param options: list of options that the mask token may be [optional]
        :param k: the number of options to output if no output list is given
                  [optional]
        :return: list of dictionaries containing the predicted token(s) and
                 their corresponding score(s).
        NOTE: If no options are given, the returned list will be length 1
        """
        if self.mlm is None:
            self._get_masked_language_model()

        if self.model in self.tag_one_transformers:
            text = text.replace("<mask>", "[MASK]")
            text = text.replace("<MASK>", "[MASK]")
        else:
            text = text.replace("[MASK]", "<mask>")

        if not self._text_verification(text):
            return

        tokenized_text = self.\
            _get_tokenized_text(text)
        masked_index = tokenized_text.index(self.masked_token)
        softmax = self._get_prediction_softmax(tokenized_text)
        if options is not None:
            option_ids = [self.tokenizer.encode(option) for option in options]

            scores = list(map(lambda x: self.soft_sum(x, softmax[0],
                                                      masked_index),
                              option_ids))
        else:
            top_predictions = torch.topk(softmax[0, masked_index], k)
            scores = top_predictions[0].tolist()
            prediction_index = top_predictions[1].tolist()
            options = self.tokenizer.convert_ids_to_tokens(prediction_index)

        tupled_predictions = tuple(zip(options, scores))

        if self.model == "XLNET": # TODO find other models that also require this
            tupled_predictions = self.__remove_staring_character(tupled_predictions, "▁")
        if self.model == "RoBERTa":
            tupled_predictions = self.__remove_staring_character(tupled_predictions, "Ġ")


        if self.gpu_support == "cuda":
            torch.cuda.empty_cache()

        return self.__format_option_scores(tupled_predictions)

    def __remove_staring_character(self, tupled_predictions, starting_char):
        """
        Some cased models like XLNet place a "▁" character in front of lower cased predictions.
        For most applications this extra bit of information is irrelevant.
        :param tupled_predictions: A list that contains tuples where the first index is
                                the name of the prediction and the second index is the
                                prediction's softmax
        ;param staring_char: The special character that is placed at the start of the predicted word
        :return: a new list of tuples where the prediction's name does not contains a special starting character
        """
        new_predictions = list()
        for prediction in tupled_predictions:
            word_prediction = prediction[0]
            if word_prediction[0] == starting_char:
                new_prediction = (word_prediction[1:], prediction[1])
                new_predictions.append(new_prediction)
            else:
                new_predictions.append(prediction)
        return new_predictions

    def _get_tokenized_text(self, text):
        """
        Formats a sentence so that it can be tokenized by a transformer.
        :param text: a 1-2 sentence text that contains [MASK]
        :return: A string with the same sentence that contains the required
                 tokens for the transformer
        """

        # Create a spacing around each punctuation character. eg "!" -> " ! "
        # TODO: easy: find a cleaner way to do punctuation spacing
        text = re.sub('([.,!?()])', r' \1 ', text)
        # text = re.sub('\s{2,}', ' ', text)

        split_text = text.split()
        new_text = list()
        new_text.append(self.cls_token)

        for i, char in enumerate(split_text):
            new_text.append(char.lower())
            if char not in string.punctuation:
                pass
            # must be a punctuation symbol
            elif i+1 >= len(split_text):
                # is the last punctuation so simply add to the new_text
                pass
            else:
                if split_text[i + 1] in string.punctuation:
                    pass
                else:
                    new_text.append(self.sep_token)
                # must be a middle punctuation
        new_text.append(self.sep_token)
        text = " ".join(new_text).replace('[mask]', self.masked_token)
        text = self.tokenizer.tokenize(text)
        return text

    def _get_prediction_softmax(self, text: str):
        """
        Gets the softmaxes of the predictions for each index in the the given
        input string.
        Returned tensor will be in shape:
            [1, <tokens in string>, <possible options for token>]
        :param text: a tokenized string to be used by the transformer.
        :return: a tensor of the softmaxes of the predictions of the
                 transformer
        """
        segments_ids = self._get_segment_ids(text)
        indexed_tokens = self.tokenizer.convert_tokens_to_ids(text)

        # Convert inputs to PyTorch tensors
        tokens_tensor = torch.tensor([indexed_tokens])
        segments_tensors = torch.tensor([segments_ids])

        with torch.no_grad():
            outputs = self.mlm(tokens_tensor,
                                       token_type_ids=segments_tensors)
            predictions = outputs[0]

            softmax = self._softmax(predictions)
            return softmax

    def __format_option_scores(self, tupled_predicitons: list):
        """
        Formats the given list of tuples containing the option and its
        corresponding score into a user friendly list of dictionaries where
        the first element in the list is the option with the highest score.
        Dictionary will be in the form:
             {'word': <the option>, 'score': <score for the option>}
        :param: ranked_scores: list of tuples to be converted into user
                friendly dicitonary
        :return: formatted_ranked_scores: list of dictionaries of the ranked
                 scores
        """
        ranked_scores = sorted(tupled_predicitons, key=lambda x: x[1],
                               reverse=True)
        formatted_ranked_scores = list()
        for word, score in ranked_scores:
            formatted_ranked_scores.append({'word': word, 'score': score})
        return formatted_ranked_scores

    def _softmax(self, value):
        # TODO: make it an external function
        return value.exp() / (value.exp().sum(-1)).unsqueeze(-1)

    def _get_segment_ids(self, tokenized_text: list):
        """
        Converts a list of tokens into segment_ids. The segment id is a array
        representation of the location for each character in the
        first and second sentence. This method only words with 1-2 sentences.
        Example:
        tokenized_text = ['[CLS]', 'who', 'was', 'jim', 'henson', '?', '[SEP]',
                          'jim', '[MASK]', 'was', 'a', 'puppet', '##eer',
                          '[SEP]']
        segments_ids = [0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1]
        returns segments_ids
        """
        split_location = tokenized_text.index(self.sep_token)
        segment_ids = list()
        for i in range(0, len(tokenized_text)):
            if i <= split_location:
                segment_ids.append(0)
            else:
                segment_ids.append(1)
            # add exception case for XLNet
        return segment_ids


    def _text_verification(self, text: str):

        # TODO,  Add cases for the other masked tokens used in common transformer models
        valid = True
        if '[MASK]' not in text:
            print("[MASK] was not found in your string. Change the word you want to predict to [MASK]")
            valid = False
        if '<mask>' in text or '<MASK>' in text:
            print('Instead of using <mask> or <MASK>, use [MASK] please as it is the convention')
            valid = True
        if '[CLS]' in text:
            print("[CLS] was found in your string.  Remove it as it will be automatically added later")
            valid = False
        if '[SEP]' in text:
            print("[SEP] was found in your string.  Remove it as it will be automatically added later")
            valid = False

        return valid


    @staticmethod
    def soft_sum(option: list, softed, mask_id: int):
        # TODO: Better logic.
        """
        Adds the softmax of a single option
        XLNET tokenizer sometimes splits words in to pieces.
        Ex: The councilmen -> ['the', 'council', 'men']
        Pretty sure that this is mathematically wrong
        :param option: Id of tokens in one option
        :param softed: softmax of the output
        :param mask: Index of masked word
        :return: float Tensor
        """
        # Collects the softmax of all tokens in list
        return np.sum([softed[mask_id][op] for op in option])


    def init_sequence_classifier(self):
        """
        Initializes a binary sequence classifier model with default settings
        """

        # TODO Test the sequence classifier with other models
        self.seq_args = classifier_args.copy()
        self.seq_args["model_type"] = self.model
        self.seq_args['model_name'] = self.model_version
        self.seq_args['gpu_support'] = self.gpu_support
        self.seq = SequenceClassifier(self.seq_args, self.tokenizer, self.logger)

        self.logger.info("A binary sequence classifier for %s has been initialized", self.model)

    def advanced_init_sequence_classifier(self, args):
        """
        Initializes a binary sequence classifier model with custom settings..
        The default settings args dicttionary can be found  happy_transformer/classifier_args.
        This dictionary can then be modified and then used as the only input for this method.

        """
        if self.model == "XLNET":
            self.seq = SequenceClassifier(args, self.tokenizer)
            self.logger.info("A binary sequence classifier for %s has been initialized", self.model)
        else:
            self.logger.error("Sequence classifier is not available for %s", self.model)

    def train_sequence_classifier(self, csv_path):
        """
        Trains the HappyTransformer's sequence classifier

        :param csv_path: A path to the csv evaluation file.
            Each test is contained within a row.
            The first column is for the the correct answers, either 0 or 1 as an int or a string .
            The second column is for the text.
        """
        self.logger.info("***** Running Training *****")


        train_df = self.__process_classifier_data(csv_path)

        if self.seq == None:
            self.logger.error("Initialize the sequence classifier before training")
            return
        sys.stdout = open(os.devnull, 'w') # Disable printing to stop external libraries from printing
        train_df = train_df.astype("str")
        self.seq.train_list_data = train_df.values.tolist()
        self.seq_args["task"] = "train"
        self.seq.train_model()
        self.seq_args["task"] = "idle"
        self.seq_trained = True
        sys.stdout = sys.__stdout__  # Enable printing


    def eval_sequence_classifier(self, csv_path):
        """
        Evaluates the trained sequence classifier against a testing set.

        :param csv_path: A path to the csv evaluation file.
            Each test is contained within a row.
            The first column is for the the correct answers, either 0 or 1 as an int or a string .
            The second column is for the text.

        :return: A dictionary evaluation matrix
        """

        self.logger.info("***** Running evaluation *****")

        sys.stdout = open(os.devnull, 'w') # Disable printing

        eval_df = self.__process_classifier_data(csv_path)

        if self.seq_trained == False:
            self.logger.error("Train the sequence classifier before evaluation")
            return
        eval_df = eval_df.astype("str")
        self.seq.eval_list_data = eval_df.values.tolist()

        self.seq_args["task"] = "eval"
        results = self.seq.evaluate()
        self.seq_args["task"] = "idle"
        sys.stdout = sys.__stdout__  # Enable printing

        return results

    def test_sequence_classifier(self, csv_path):
        """

        :param csv_path: a path to the csv evaluation file.
            Each test is contained within a row.
            The first column is for the the correct answers, either 0 or 1 as an int or a string .
            The second column is for the text.
        :return: A list of predictions where each prediction index is the same as the corresponding test's index
        """
        self.logger.info("***** Running Testing *****")
        # sys.stdout = open(os.devnull, 'w') # Disable printing

        test_df = self.__process_classifier_data(csv_path, for_test_data=True)

        # todo finish
        if self.seq_trained == False:
            self.logger.error("Train the sequence classifier before testing")
            return

        test_df = test_df.astype("str")
        self.seq.test_list_data = test_df.values.tolist()

        self.seq_args["task"] = "test"
        results = self.seq.test()
        self.seq_args["task"] = "idle"

        sys.stdout = sys.__stdout__  # Enable printing

        return results

    def __process_classifier_data(self, csv_path, for_test_data=False):
        """
        :param csv_path: Path to csv file that must be processed
        :return: A Panda dataframe with the proper information for classification tasks
        """

        if for_test_data:
            with open(csv_path, 'r') as test_file:
                reader = csv.reader(test_file)
                text_list = list(reader)
            # Blank values are required for the first column value the testing data to increase
            # reusability of preprocessing methods between the tasks
            blank_values= ["-1"]*len(text_list)
            df = pd.DataFrame([*zip(blank_values, text_list)])
            print(df.head())

        else:
            df = pd.read_csv(csv_path, header=None)

        df[0] = (df[0] == 2).astype(int)
        df = pd.DataFrame({
            'id': range(len(df)),
            'label': df[0],
            'alpha': ['a'] * df.shape[0],
            'text': df[1].replace(r'\n', ' ', regex=True)
        })

        return df
