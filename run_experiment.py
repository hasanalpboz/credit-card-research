import os
import pandas as pd
import numpy as np
from os.path import join, exists, basename
import argparse
import yaml
from datetime import datetime
from tqdm import tqdm
from sklearn.ensemble import RandomForestClassifier
from sklearn.svm import SVC
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import GridSearchCV, StratifiedKFold
import logging

try:
    from xgboost import XGBClassifier
    GBClassifier = XGBClassifier
except ImportError:
    print(('WARNING: xgboost not installed. ',
           'Using sklearn.ensemble.GradientBoostingClassifier instead.'))
    from sklearn.ensemble import GradientBoostingClassifier
    GBClassifier = GradientBoostingClassifier

import warnings
warnings.filterwarnings('ignore')


logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
ch = logging.FileHandler('.explogfile', 'w')
ch.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s ~~ %(message)s', datefmt='%d-%b-%y %H:%M:%S')
ch.setFormatter(formatter)
logger.addHandler(ch)


def load_data(label_filepath, feature_filepath_list):

    label_df = pd.read_csv(label_filepath, index_col=0)
    logger.debug('label shape: {}, {}'.format(label_df.shape, label_filepath))

    feature_df_list = []
    for feature_filepath in feature_filepath_list:
        f_df = pd.read_csv(feature_filepath, index_col=0)
        filename = basename(feature_filepath).split('.')[0]
        f_df.columns = map(lambda x: '{}__{}'.format(filename, x), f_df.columns.tolist())
        feature_df_list.append(f_df)
    feature_df = pd.concat(feature_df_list, axis=1)

    label_merchantid_set = set(label_df.index.tolist())
    feature_merchantid_set = set(feature_df.index.tolist())

    instance_ind = list(label_merchantid_set & feature_merchantid_set)
    assert len(instance_ind) > 0

    logger.debug('label_merchantid_set: {}'.format(len(label_merchantid_set)))
    logger.debug('feature_merchantid_set: {}'.format(len(feature_merchantid_set)))
    logger.debug('intersection: {}'.format(len(label_merchantid_set & feature_merchantid_set)))

    filtered_feature_df = feature_df.loc[instance_ind]

    X = filtered_feature_df.fillna(0).values
    y = label_df.loc[instance_ind].values.ravel().astype(int)

    assert len(np.unique(y)) == 2, 'non-binary class labels are provided'
    assert X.shape[0] == len(y), 'number of records do not match'

    return X, y, filtered_feature_df


def run_cross_validation(X, y, feature_df=None):
    test_auc_dict = {}
    fimp_dict = {}
    for clf_name, clf in [('lr', GridSearchCV(
                                LogisticRegression(),
                                param_grid={"C": [0.001, 0.01, 0.1, 1.0, 10.0]},
                                scoring='roc_auc'
                            )),
                            ('xgboost', GridSearchCV(
                                XGBClassifier(objective='binary:logistic', eval_metric='auc'),
                                param_grid={'n_estimators': [10, 100],
                                            'learning_rate': [0.01, 0.05],
                                            'max_depth': [2, 5]},
                                scoring='roc_auc'
                            )),
                            ('rf', GridSearchCV(
                                RandomForestClassifier(),
                                    param_grid={'n_estimators': [10, 100],
                                                'max_depth': [3, 5, 10, 20]
                                },
                                scoring='roc_auc'
                            ))]:
        skf = StratifiedKFold(n_splits=5,
                              shuffle=True,
                              random_state=1)
        test_auc_list = []
        fimp_list = []
        for train_index, test_index in skf.split(X, y):
            X_train, X_test = X[train_index, :], X[test_index, :]
            y_train, y_test = y[train_index], y[test_index]
            scaler = StandardScaler()
            X_train = scaler.fit_transform(X_train)
            X_test = scaler.transform(X_test)
            clf.fit(X_train, y_train)
            test_auc = roc_auc_score(
                y_test,
                clf.best_estimator_.predict_proba(X_test)[:, 1])
            test_auc_list.append(test_auc)
            # feature importance
            if clf_name == 'lr':
                fimp = clf.best_estimator_.coef_[0]
            elif clf_name == 'xgboost':
                fimp = clf.best_estimator_.feature_importances_
            fimp_list.append(fimp)

        test_auc_dict[clf_name] = test_auc_list
        fimp_df = pd.DataFrame(fimp_list, columns=feature_df.columns)
        fimp_dict[clf_name] = fimp_df

    test_auc_df = pd.DataFrame(test_auc_dict)
    mean_df = pd.DataFrame(test_auc_df.mean(axis=0)).T
    mean_df.index = ["mean"]
    std_df = pd.DataFrame(test_auc_df.std(axis=0)).T
    std_df.index = ["std"]

    eval_df = pd.concat([test_auc_df, mean_df, std_df], axis=0)

    return eval_df, fimp_dict


def create_filename(label_filepath, feature_filepath_list):
    prefix = basename(label_filepath).split('.')[0]
    suffix = '_'.join(list(map(lambda x: basename(x).split('.')[0], feature_filepath_list)))
    return '{}_{}.csv'.format(prefix, suffix)


def run_experiment(label_filepath, feature_filepath_list, output_dirpath):
    assert exists(output_dirpath), f'{output_dirpath} does not exists'

    output_filepath = join(output_dirpath, create_filename(label_filepath, feature_filepath_list))
    
    X, y, feature_df = load_data(label_filepath, feature_filepath_list)

    eval_df, fimp_dict = run_cross_validation(X, y, feature_df)
    eval_df.to_csv(output_filepath)

    for clf_name, fimp_df in fimp_dict.items():
        cur_filepath = output_filepath.replace('.csv', '_fimp_{}.csv'.format(clf_name))
        fimp_mean_s = fimp_df.mean(axis=0)
        fimp_std_s = fimp_df.std(axis=0)
        fimp_info_df = pd.DataFrame({'fimp_mean': fimp_mean_s, 'fimp_std': fimp_std_s})
        fimp_info_df.sort_values('fimp_mean', ascending=False).to_csv(cur_filepath)

    return eval_df, fimp_info_df


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Running experiments with different feature combinations')
    
    parser.add_argument(
        '-F', 
        '--features',
        nargs='*',
        type=str,
        required=True,
        help='feature file names'
    )

    parser.add_argument(
        '-L', 
        '--label',
        type=str,
        required=True,
        help='Label file name'
    )

    parser.add_argument(
        '-O', 
        '--outputdir',
        type=str,
        default="eval",
        help='Output directory path'
    )

    parser.add_argument(
        '-E',
        '--expname',
        type=str,
        required=False,
        help='create a sub-directory for your results'
    )

    args = parser.parse_args()
    feature_filepath_list = sorted(args.features)

    label_filepath = args.label
    output_dirpath = args.outputdir
    expname = args.expname

    if not exists(output_dirpath):
        os.mkdir(output_dirpath)

    if expname and not exists(join(output_dirpath, expname)):
        os.mkdir(join(output_dirpath, expname))
        output_dirpath = join(output_dirpath, expname)

    eval_df, fimp_info_df = run_experiment(label_filepath, feature_filepath_list, output_dirpath)