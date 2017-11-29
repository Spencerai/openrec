from tqdm import tqdm
import math
from termcolor import colored
import numpy as np
from openrec.utils.evaluators import EvalManager

class ModelTrainer(object):

    def __init__(self, batch_size, test_batch_size, item_serving_size=None, train_dataset=None, model=None, sampler=None):

        self._batch_size = batch_size
        self._test_batch_size = test_batch_size
        self._item_serving_size = item_serving_size

        self._train_dataset = train_dataset
        self._max_item = self._train_dataset.max_item

        self._model = model
        self._sampler = sampler

    def train(self, num_itr, display_itr, eval_datasets=[], evaluators=[], num_negatives=None):

        acc_loss = 0
        self._eval_manager = EvalManager(evaluators=evaluators)
        self._num_negatives = num_negatives
        self._exclude_positives(eval_datasets)

        if self._num_negatives is None:
            eval_func = self._evaluate_full
            print colored('== Start training with FULL evaluation ==', 'blue')
        else:
            eval_func = self._evaluate_partial
            self._sample_nagatives()
            print colored('== Start training with sampled evaluation, sample size: %d ==' % num_negatives, 'blue')

        for itr in range(num_itr):
            batch_data = self._sampler.next_batch()
            loss = self._model.train(batch_data)
            acc_loss += loss

            if itr % display_itr == 0 and itr > 0:
                print colored('[Itr %d]' % itr, 'red'), 'loss: %f' % (acc_loss/display_itr)
                for dataset in eval_datasets:
                    print colored('..(dataset: %s) evaluation' % dataset.name, 'green')
                    eval_results = eval_func(eval_dataset=dataset)
                    for key, result in eval_results.items():
                        average_result = np.mean(result, axis=0)
                        if type(average_result) is np.ndarray:
                            print colored('..(dataset: %s)' % dataset.name, 'green'), \
                                key, ' '.join([str(s) for s in average_result])
                        else:
                            print colored('..(dataset: %s)' % dataset.name, 'green'), \
                                key, average_result
                acc_loss = 0

        return loss

    def _score_full_items(self, users):

        if self._item_serving_size is None:
            return self._model.serve({'user_id_input': users})
        else:
            scores = []
            item_id_input = np.zeros(self._item_serving_size, np.int32)
            for ibatch in range(int(math.ceil(float(self._max_item) / self._item_serving_size))):
                item_id_list = range(ibatch*self._item_serving_size,
                                min((ibatch+1)*self._item_serving_size, self._max_item))
                item_id_input[:len(item_id_list)] = item_id_list
                scores.append(self._model.serve({'user_id_input': users, 
                                                'item_id_input': item_id_input})[:len(item_id_list)])
            return np.concatenate(scores, axis=1)

    def _score_partial_items(self, user, items):

        if self._item_serving_size is None:
            return self._model.serve({'user_id_input': [user]})[0][np.array(items)]
        else:
            return self._model.serve({'user_id_input': [user], 
                               'item_id_input': np.array(items)})[0]

    def _evaluate_full(self, eval_dataset):

        metric_results = {}
        for evaluator in self._eval_manager.evaluators:
            metric_results[evaluator.name] = []
            
        for itr in tqdm(range(int(math.ceil(float(eval_dataset.num_user) / self._test_batch_size)))):
            users = eval_dataset.users[itr * self._test_batch_size:(itr + 1) * self._test_batch_size]
            scores = self._score_full_items(users=users)
            for u_ind, user in enumerate(users):
                result = self._eval_manager.full_eval(pos_samples=eval_dataset.gy_user_item[user].keys(),
                                        excl_pos_samples=self._excluded_positives[user],
                                                    predictions=scores[u_ind])
                for key in result:
                    metric_results[key].append(result[key])

        return metric_results

    def _evaluate_partial(self, eval_dataset):

        metric_results = {}
        for evaluator in self._eval_manager.evaluators:
            metric_results[evaluator.name] = []

        for user in tqdm(eval_dataset.users):
            items = self._sampled_negatives[user] + eval_dataset.gy_user_item[user].keys()
            scores = self._score_partial_items(user, items)
            result = self._eval_manager.partial_eval(pos_scores=scores[self._num_negatives:], neg_scores=scores[:self._num_negatives])
            for key in result:
                metric_results[key].append(result[key])

        return metric_results

    def _exclude_positives(self, eval_datasets):
        
        self._excluded_positives = {}
        user_set = set()

        for dataset in eval_datasets:
            user_set = user_set.union(dataset.users)
        for user in user_set:
            self._excluded_positives[user] = set()
        for user in user_set:
            if user in self._train_dataset.gy_user_item:
                self._excluded_positives[user] = self._excluded_positives[user].union(self._train_dataset.gy_user_item[user].keys())
            for dataset in eval_datasets:
                if user in dataset.gy_user_item:
                    self._excluded_positives[user] = self._excluded_positives[user].union(dataset.gy_user_item[user].keys())


    def _sample_nagatives(self):

        print colored('[Subsampling negative items]', 'red')
        self._sampled_negatives = {}
        for user in tqdm(self._excluded_positives):
            shuffled_items = np.random.permutation(self._max_item)
            subsamples = []
            for item in shuffled_items:
                if item not in self._excluded_positives[user]:
                    subsamples.append(item)
                if len(subsamples) == self._num_negatives:
                    break
            self._sampled_negatives[user] = subsamples
