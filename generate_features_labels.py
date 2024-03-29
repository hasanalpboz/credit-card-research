import pandas as pd
import numpy as np
from os.path import join, exists
import argparse
import yaml
from datetime import datetime
import igraph as ig
from tqdm import tqdm
import logging
from tqdm import tqdm
import os

logfname = '.featurelogfile'
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
ch = logging.FileHandler(logfname, 'w')
ch.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s ~~ %(message)s', datefmt='%d-%b-%y %H:%M:%S')
ch.setFormatter(formatter)
logger.addHandler(ch)


def calc_entropy(s):
    '''
    calcualtes shannon-entropy for the given set of values
    '''
    if not isinstance(s, pd.Series):
        s = pd.Series(s, dtype=str)
    count_s = s.value_counts()
    prob_s = count_s / count_s.sum()
    return (prob_s * np.log(1.0 / prob_s)).sum()


def create_network(config, bank, fname_prefix, weights='weight'):
    '''
    creates merchant networks for the given bank type
    '''
    # fname = f'network_features_{weights}_{bank}.csv'
    fname = f'filtered_network_features_{weights}_{bank}.csv'
    if fname_prefix:
        fname = f'{fname_prefix}_{fname}'

    output_fname = join('features', fname)

    if exists(output_fname):
        print(f'{output_fname} already exists')
        return

    print('extracting network features')

    g = ig.Graph(directed=False)
    # g = g.Read_Pickle(join('data', 'networks', f'bank_{bank}.pickle'))
    g = g.Read_Pickle(join('data', 'networks', f'filtered_bank_{bank}.pickle'))

    trans_cols = config['tran_cols'][f'bank_{bank}']
    trans_fname = f'filtered_bank_{bank}_trans.csv'
    if fname_prefix:
        trans_fname = f'{fname_prefix}_{trans_fname}'
    transaction_df = pd.read_csv(join('data', 'filtered_data', trans_fname), 
                                      usecols=[trans_cols['merchant_id']], dtype={trans_cols['merchant_id']: int})

    # get filtered merchants
    merchants = transaction_df[trans_cols['merchant_id']].astype(str).unique()
    net_df = pd.DataFrame(merchants, columns=['merchant_id'])

    nodes = np.array([node['name'] for node in g.vs])
    node2ind = [np.where(nodes == merchant)[0][0] for merchant in merchants]

    mcc_div = []
    dist_div = []
    for merchant in tqdm(merchants, desc='Extracting merchant mcc/district diversity metrics'):
        mcc_div.append(calc_entropy([g.vs[n]['mcc'] for n in g.neighbors(merchant)]))
        dist_div.append(calc_entropy([g.vs[n]['district_id'] for n in g.neighbors(merchant)]))

    # create the diversity features
    net_df['mcc_div'] = mcc_div
    net_df['district_div'] = dist_div

    # add merchant mcc and district ids as well
    net_df['mcc'] = np.array(g.vs['mcc'])[node2ind]
    net_df['district_id'] = np.array(g.vs['district_id'])[node2ind]

    # assign network centrality metrics
    models = [('pr', g.pagerank, {'weights': weights, 'directed': False, 'damping': 0.85}),
              ('degree', g.degree, {'mode': 'all'}),
              ('closeness', g.closeness, {'weights': weights, 'mode': 'all', 'cutoff': None, 'normalized': True}),
              ('betweenness', g.betweenness, {'weights': weights, 'directed': False, 'cutoff': None}),
              ('eigenvector', g.eigenvector_centrality, {'weights': weights, 'directed': False})
             ]

    for name, func, params in tqdm(models, desc='centrality metrics'):
        if name == 'eigenvector':
            eigs = np.array(func(**params))
            net_df[name] = eigs[node2ind]
        else:
            net_df[name] = func(vertices=merchants, **params)

    logger.debug('bank {}, network features, # of rows: {}, # of columns: {}'.format(bank, net_df.shape[0], net_df.shape[1]))
    logger.debug('bank {}, nan_values: {}'.format(bank, net_df.isna().sum()))

    net_df = net_df.set_index('merchant_id')
    net_df.index = net_df.index.astype(int)
    net_df.to_csv(output_fname)


def create_demographics(config, bank, fname_prefix):
    '''
    construct demographic features
    '''
    fname = f'demographics_{bank}.csv'
    if fname_prefix:
        fname = f'{fname_prefix}_{fname}'

    output_fname = join('features', fname)

    if exists(output_fname):
        print(f'{output_fname} already exists')
        return

    print('extracting demographic features')

    trans_cols = config['tran_cols'][f'bank_{bank}']
    customer_cols = config['customer_cols'][f'bank_{bank}']

    trans_fname = f'filtered_bank_{bank}_trans.csv'
    if fname_prefix:
        trans_fname = f'{fname_prefix}_{trans_fname}'
    transaction_df = pd.read_csv(join('data', 'filtered_data', trans_fname), dtype={trans_cols['merchant_id']: int})
    customer_df = pd.read_csv(join('data', 'filtered_data', f'bank_{bank}_customers_districts.csv'))

    income = customer_cols['income']
    age = customer_cols['age']
    home_district = 'home_district_id'
    work_district = 'work_district_id'
    gender = customer_cols['gender']
    education = customer_cols['education']
    emp = customer_cols['employment']
    marital_st = customer_cols['marital_status']

    date_format = config['break_date']['date_format']
    break_date = datetime.strptime(config['break_date'][f'bank_{bank}'], date_format)

    # filter by break date
    transaction_df[trans_cols['tran_date']] = pd.to_datetime(transaction_df[trans_cols['tran_date']], format=date_format)
    transaction_df = transaction_df[transaction_df[trans_cols['tran_date']] <= break_date]

    merchantid_list = []
    feature_dict_list = []
    for merchantid, group_df in transaction_df.groupby([trans_cols['merchant_id']]):
    #for merchantid, group_df in transaction_df[transaction_df[trans_cols['mcc']].isin([5411])].groupby([trans_cols['merchant_id']]):
        customerid_set = set(group_df[trans_cols['customer_id']].tolist())
        cur_df = customer_df[customer_df[customer_cols['customer_id']].isin(customerid_set)]

        feature_dict = {
            # income
            'income_median': cur_df[income].median(),
            'income_mean': cur_df[income].mean(),
            'income_std': cur_df[income].std(),

            # age
            'age_median': cur_df[age].median(),
            'age_mean': cur_df[age].mean(),
            'age_std': cur_df[age].std(),

            # home-work district entropy
            'home_dist_ent': calc_entropy(cur_df[home_district]),
            'work_dist_ent': calc_entropy(cur_df[work_district]),

            # demographic entropy
            'gender_ent': calc_entropy(cur_df[gender]),
            'education_ent': calc_entropy(cur_df[education]),
            'marital_ent': calc_entropy(cur_df[marital_st]),
            'employment_ent': calc_entropy(cur_df[emp])}

        merchantid_list.append(merchantid)
        feature_dict_list.append(feature_dict)

    feature_df = pd.DataFrame(feature_dict_list)
    feature_df.index = merchantid_list
    logger.debug('bank {}, demographic features, # of rows: {}, # of columns: {}'.format(bank, feature_df.shape[0], feature_df.shape[1]))
    logger.debug('bank {}, nan_values: {}'.format(bank, feature_df.isna().sum()))
    feature_df.to_csv(output_fname)


def create_revenue_features(config, bank, fname_prefix):
    '''
    create features based on revenur for the given bank type 
    '''
    fname = f'revenue_{bank}.csv'
    if fname_prefix:
        fname = f'{fname_prefix}_{fname}'

    output_fname = join('features', fname)

    if exists(output_fname):
        print(f'{output_fname} already exists')
        return

    print('extracting revenue features')

    trans_cols = config['tran_cols'][f'bank_{bank}']

    trans_fname = f'filtered_bank_{bank}_trans.csv'
    if fname_prefix:
        trans_fname = f'{fname_prefix}_{trans_fname}'
    transaction_df = pd.read_csv(join('data', 'filtered_data', trans_fname), dtype={trans_cols['merchant_id']: int})

    date_format = config['break_date']['date_format']
    break_date = datetime.strptime(config['break_date'][f'bank_{bank}'], date_format)

    # filter by break date
    transaction_df[trans_cols['tran_date']] = pd.to_datetime(transaction_df[trans_cols['tran_date']], format=date_format)
    transaction_df = transaction_df[transaction_df[trans_cols['tran_date']] <= break_date]
    # transaction_df = transaction_df[transaction_df[trans_cols['mcc']].isin([5411])]

    group = transaction_df.groupby(trans_cols['merchant_id'])

    revenue_sum = group.agg({trans_cols['tran_amount']: ['size', 'sum']})
    revenue_sum.columns = ['trans_count', 'total_revenue']

    nunique_cust = group[[trans_cols['customer_id']]].apply(lambda x: np.unique(x).shape[0]).rename('unique_num_customers')

    monthly_rev = transaction_df.groupby([trans_cols['merchant_id'], 'yyyymm'])[trans_cols['tran_amount']].agg(['mean', 'std']).unstack()
    monthly_rev.columns = monthly_rev.columns.to_flat_index()

    rev = pd.concat([revenue_sum, nunique_cust, monthly_rev], axis=1)
    logger.debug('bank {}, revenue features, # of rows: {}, # of columns: {}'.format(bank, rev.shape[0], rev.shape[1]))
    logger.debug('bank {}, nan_values: {}'.format(bank, rev.isna().sum()))
    rev.to_csv(output_fname)


def generate_labels(config, bank, fname_prefix):
    '''
    generate merchant well-being labels based on revenu
    '''
    fname = f'labels_{bank}.csv'
    if fname_prefix:
        fname = f'{fname_prefix}_{fname}'
    output = join('labels', fname)

    if exists(output):
        print(f'{output} already exists')
        return

    print('preparing labels')

    trans_cols = config['tran_cols'][f'bank_{bank}']

    date_format = config['break_date']['date_format']
    break_date = datetime.strptime(config['break_date'][f'bank_{bank}'], date_format)

    agg_trans_fname = f'agg_bank_{bank}_trans.csv'
    if fname_prefix:
        agg_trans_fname = f'{fname_prefix}_{agg_trans_fname}'
    df = pd.read_csv(join('data', 'filtered_data', agg_trans_fname), index_col=[0, 1, 2], header=[0, 1])
    
    # split transaction summaries into two halves
    df = df[trans_cols['tran_amount']].reset_index(level=2)
    df[trans_cols['tran_date']] = pd.to_datetime(df[trans_cols['tran_date']], format=date_format)

    fh = df[df[trans_cols['tran_date']] <= break_date]  # first half
    sh = df[df[trans_cols['tran_date']] > break_date]   # second half

    # merchant id to mcc 
    mid2mcc = dict(df.index.drop_duplicates())

    # get avg monthly revenues
    avg_fh = fh.groupby([fh.index.get_level_values(0), fh[trans_cols['tran_date']].dt.month])['sum'] \
            .sum().groupby(level=0).mean()
    avg_sh = sh.groupby([sh.index.get_level_values(0), sh[trans_cols['tran_date']].dt.month])['sum'] \
                .sum().groupby(level=0).mean()

    assert set(avg_fh.index) == set(avg_sh.index), 'merchant id mismatch in labels'

    revenue_change = ((avg_sh.loc[avg_fh.index] - avg_fh) / avg_fh).rename('label')
    #median_change = revenue_change.median()
    mcc_median_change = revenue_change.groupby(lambda x: mid2mcc[x]).median()

    gt_median = [revenue_change.loc[ind] >= mcc_median_change[mid2mcc[ind]] for ind in revenue_change.index]
    ls_median = [revenue_change.loc[ind] < mcc_median_change[mid2mcc[ind]] for ind in revenue_change.index]
    
    revenue_change.loc[gt_median] = 1
    revenue_change.loc[ls_median] = 0

    logger.debug('bank {}, labels, # of rows: {}'.format(bank, revenue_change.shape[0]))
    logger.debug('bank {}, nan_values: {}'.format(bank, revenue_change.isna().sum()))
    logger.debug('bank {}, label distribution: {}'.format(bank, revenue_change.value_counts(normalize=True)))
    revenue_change.to_csv(output)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Create revenue/demographic/network features and well-being labels')

    parser.add_argument(
        '-B', 
        '--bank',
        type=str,
        required=True,
        help='bank name ("x", "y" or custom)'
    )

    parser.add_argument(
        '-P',
        '--prefix',
        type=str,
        required=False,
        help='output file name prefix'
    )

    parser.add_argument(
        '-W',
        '--weight',
        type=str,
        required=False,
        help='weighted network features'
    )

    args = parser.parse_args()
    bank = args.bank.lower()
    fname_prefix = args.prefix
    weight = args.weight

    with open(join('config.yaml')) as f:
        config = yaml.safe_load(f)

    generate_labels(config, bank, fname_prefix)
    create_demographics(config, bank, fname_prefix)
    create_network(config, bank, fname_prefix, weights=weight)
    create_revenue_features(config, bank, fname_prefix)