# -*- coding: utf-8 -*- 

from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_http_methods
from django.views.generic.simple import direct_to_template

import datetime
import os
import pymongo
import mongo.search_variants as search_variants
import mongo.risk_report as risk_report
import numpy as np


from pprint import pprint
import colors

from apps.riskreport.forms import RiskReportForm

from django.conf import settings


def get_risk_values(tmp_infos):
    """ """

    # TODO: merge to get_risk_infos

    for i, tmp_info in enumerate(tmp_infos):
        # calculate risk
        population = 'population:{}'.format('+'.join(settings.POPULATION_MAP[tmp_info['population']]))
        catalog_map, variants_map = search_variants.search_variants(tmp_info['user_id'], tmp_info['name'], population)
        risk_store, risk_reports = risk_report.risk_calculation(catalog_map, variants_map, settings.POPULATION_CODE_MAP[tmp_info['population']],
                                                                tmp_info['sex'], tmp_info['user_id'], tmp_info['name'], 
                                                                False,  True, None)
                                                                # os.path.join(settings.UPLOAD_DIR, user_id, '{}_{}.p'.format(tmp_info['user_id'], tmp_info['name'])))

        # list for chart
        tmp_map = {}
        for trait,studies in risk_reports.items():
            tmp_map[trait] = np.mean(studies.values())

        if i == 0:
            risk_traits = [k for k,v in sorted(tmp_map.items(), key=lambda(k,v):(v,k), reverse=True)]
            risk_values = [[v for k,v in sorted(tmp_map.items(), key=lambda(k,v):(v,k), reverse=True)]]

        elif i >= 1:
            risk_values.append([tmp_map.get(trait, 0) for trait in risk_traits])

    return risk_reports, risk_traits, risk_values


def get_risk_infos(user_id, file_name, trait_name=None, study_name=None):
    msg, err = '', ''
    infos, tmp_info, tmp_risk_store  = None, None, None
    RR_list, RR_list_real, study_list, snps_list = [], [], [], []

    with pymongo.Connection(port=settings.MONGO_PORT) as connection:
        db = connection['pergenie']
        data_info = db['data_info']

        while True:
            # determine file
            infos = list(data_info.find( {'user_id': user_id} ))
            tmp_info = None

            if not infos:
                err = 'データがアップロードされていません．'
                # err = 'no data uploaded'
                break

            for info in infos:
                if info['name'] == file_name:
                    tmp_info = info
                    break
            if not tmp_info:
                err = 'そのようなファイルはありません．{}'.format(file_name)
                # err = 'no such file {}'.format(file_name)
                break

            print '[DEBUG] tmp_info', tmp_info

            # calculate risk
            population = 'population:{}'.format('+'.join(settings.POPULATION_MAP[tmp_info['population']]))
            catalog_map, variants_map = search_variants.search_variants(tmp_info['user_id'], tmp_info['name'], population)
            risk_store, risk_reports = risk_report.risk_calculation(catalog_map, variants_map, settings.POPULATION_CODE_MAP[tmp_info['population']],
                                                                    tmp_info['sex'], tmp_info['user_id'], tmp_info['name'],
                                                                    False, True, None)
                                                                    # os.path.join(settings.UPLOAD_DIR, user_id, '{}_{}.p'.format(tmp_info['user_id'], tmp_info['name'])))


            if study_name and trait_name:
                tmp_risk_store = risk_store.get(trait_name).get(study_name)

                snps_list = [k for k,v in sorted(tmp_risk_store.items(), key=lambda x:x[1]['RR'])]
                RR_list = [v['RR'] for k,v in sorted(tmp_risk_store.items(), key=lambda x:x[1]['RR'])]

                if not trait_name.replace('_', ' ') in tmp_risk_store:
                    err = 'そのようなtraitはありません'
                    # err = 'trait not found'
                    break

            elif not study_name and trait_name:
                tmp_risk_store = risk_store.get(trait_name)
                tmp_study_value_map = risk_reports.get(trait_name)
                study_list = [k for k,v in sorted(tmp_study_value_map.items(), key=lambda(k,v):(v,k), reverse=True)]

                # list for chart
                RR_list = [v for k,v in sorted(tmp_study_value_map.items(), key=lambda(k,v):(v,k), reverse=True)]
                RR_list_real = [round(10**v, 3) for k,v in sorted(tmp_study_value_map.items(), key=lambda(k,v):(v,k), reverse=True)]

            else:
                pass

            break

        risk_infos = {'msg':msg, 'err': err, 'infos': infos, 'tmp_info': tmp_info,
                      'RR_list': RR_list, 'RR_list_real': RR_list_real, 'study_list': study_list,
                      'file_name': file_name, 'trait_name': trait_name, 'study_name': study_name,
                      'snps_list': snps_list, 'tmp_risk_store': tmp_risk_store}

        print '[DEBUG]',
        pprint(risk_infos)

        return risk_infos
        

@require_http_methods(['GET', 'POST'])
@login_required
def index(request):
    user_id = request.user.username
    msg = ''
    err = ''
    risk_reports = None
    risk_traits = None
    risk_values = None

    with pymongo.Connection(port=settings.MONGO_PORT) as connection:
        db = connection['pergenie']
        data_info = db['data_info']

        while True:
            # determine file
            infos = list(data_info.find( {'user_id': user_id} ))
            tmp_info = None
            tmp_infos = []

            if not infos:
                err = 'データがアップロードされていません．'
                # err = 'no data uploaded'
                break
            print infos

            if request.method == 'POST':
                print '[DEBUG] method = POST'
                form = RiskReportForm(request.POST)
                if not form.is_valid():
                    err = '不正なリクエストです．'
                    # err = 'Invalid request'
                    break

                for i, file_name in enumerate([request.POST['file_name'], request.POST['file_name2']]):
                    print '[DEBUG] file_name{0} from form: {1}'.format(i+1, file_name)

                    for info in infos:
                        print info['name'], bool(info['name'] == file_name)
                        if info['name'] == file_name:

                            if not info['status'] == 100:
                                err = '{} は現在読み込み中です．しばらくお待ちください...'.format(file_name)
                                # err = '{} is in importing, please wait for seconds...'.format(file_name)

                            tmp_info = info
                            tmp_infos.append(tmp_info)
                            break

                    if not tmp_info:
                        err = '{} does not exist'.format(file_name)
                        break

            else:
                print '[DEBUG] method != POST'
                info = infos[0]
                file_name = info['name']
                if not info['status'] == 100:
                    err = '{} は現在読み込み中です．しばらくお待ちください...'.format(file_name)
                    # err = '{} is in importing, please wait for seconds...'.format(file_name)
                tmp_infos.append(info)

            print '[INFO] tmp_infos', tmp_infos

            if not err:
                risk_reports, risk_traits, risk_values = get_risk_values(tmp_infos)

            break

        return direct_to_template(request,
                                  'risk_report.html',
                                  {'msg': msg,
                                   'err': err,
                                   'infos': infos,
                                   'tmp_infos': tmp_infos,
                                   'risk_reports': risk_reports,
                                   'risk_traits': risk_traits,
                                   'risk_values': risk_values})


@login_required
def trait(request, file_name, trait):
    """
    show risk value by studies, for each trait
    """

    user_id = request.user.username
    risk_infos = get_risk_infos(user_id, file_name, trait)

    return direct_to_template(request, 'risk_report_trait.html', risk_infos)


@login_required
def study(request, file_name, trait, study_name):
    """
    show RR by rss, for each study
    """

    user_id = request.user.username
    risk_infos = get_risk_infos(user_id, file_name, trait_name=trait, study_name=study_name)

    return direct_to_template(request, 'risk_report_study.html', risk_infos)
