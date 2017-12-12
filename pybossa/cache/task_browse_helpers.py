from werkzeug.exceptions import BadRequest
from collections import defaultdict
from pybossa.util import convert_est_to_utc
import re
import json


def get_task_filters(args):
    """
    build the WHERE part of the query using the filter parameters
    return the part of the WHERE clause and the dictionary of bound parameters
    """
    filters = ''
    params = {}

    if args.get('task_id'):
        params['task_id'] = args['task_id']
        filters += ' AND task.id = :task_id'
    if args.get('hide_completed') and args.get('hide_completed') is True:
        filters += " AND task.state='ongoing'"
    if args.get('pcomplete_from') is not None:
        params['pcomplete_from'] = args['pcomplete_from']
        filters += " AND (coalesce(ct, 0)/task.n_answers) >= :pcomplete_from"
    if args.get('pcomplete_to') is not None:
        params['pcomplete_to'] = args['pcomplete_to']
        filters += " AND (coalesce(ct, 0)/task.n_answers) <= :pcomplete_to"
    if args.get('priority_from') is not None:
        params['priority_from'] = args['priority_from']
        filters += " AND priority_0 >= :priority_from"
    if args.get('priority_to') is not None:
        params['priority_to'] = args['priority_to']
        filters += " AND priority_0 <= :priority_to"
    if args.get('created_from'):
        datestring = convert_est_to_utc(args['created_from']).isoformat()
        params['created_from'] = datestring
        filters += " AND task.created >= :created_from"
    if args.get('created_to'):
        datestring = convert_est_to_utc(args['created_to']).isoformat()
        params['created_to'] = datestring
        filters += " AND task.created <= :created_to"
    if args.get('ftime_from'):
        datestring = convert_est_to_utc(args['ftime_from']).isoformat()
        params['ftime_from'] = datestring
        filters += " AND ft >= :ftime_from"
    if args.get('ftime_to'):
        datestring = convert_est_to_utc(args['ftime_to']).isoformat()
        params['ftime_to'] = datestring
        filters += " AND ft <= :ftime_to"
    if args.get('order_by'):
        args['order_by'].replace('pcomplete', '(coalesce(ct, 0)/task.n_answers)')
    if args.get('filter_by_field'):
        filter_query, filter_params = _get_task_info_filters(
            args['filter_by_field'])
        filters += filter_query
        params.update(**filter_params)
    return filters, params


def _escape_like_param(string):
    string = string.replace('\\', '\\\\')
    string = string.replace('%', '\\%')
    string = string.replace('_', '\\_')
    return string


op_to_query = {
    'starts with': dict(
        query="COALESCE(task.info->>'{}', '') ilike :{} escape '\\'",
        value="{}%",
        escape=_escape_like_param),
    'contains': dict(
        query="COALESCE(task.info->>'{}', '') ilike :{} escape '\\'",
        value="%{}%",
        escape=_escape_like_param),
    'equals': dict(
        query="lower(COALESCE(task.info->>'{}', '')) = lower(:{})",
        value="{}",
        escape=lambda x: x)
}


def _get_task_info_filters(filter_args):
    params = {}
    grouped_filters = _reduce_filters(filter_args)
    ix = 0
    and_pieces = []
    for field_name, ops in grouped_filters.iteritems():
        or_pieces = []
        for operator, field_value in ops:
            query, p_name, p_val = _get_or_piece(field_name, operator,
                                                 field_value, ix)
            or_pieces.append(query)
            params[p_name] = p_val
            ix += 1
        and_pieces.append('({})'.format(' OR '.join(or_pieces)))
    filter_query = ''.join(' AND {}'.format(piece) for piece in and_pieces)
    return filter_query, params


def _get_or_piece(field_name, operator, field_value, arg_index):
    if operator not in op_to_query:
        raise BadRequest("Invalid Operator")
    op = op_to_query[operator]
    param_name = 'filter_by_field_{}'.format(arg_index)
    param_value = (op['value'].format(op['escape'](field_value)))
    query_filter = op['query'].format(field_name, param_name)
    return query_filter, param_name, param_value


def _reduce_filters(filter_args):
    def reducer(acc, next_val):
        field_name, operator, field_value = next_val
        acc[field_name].append((operator, field_value))
        return acc
    return reduce(reducer, filter_args, defaultdict(list))


def is_valid_searchable_column(column_name):
    valid_str = r'[\w\-]{1,40}$'
    is_valid = re.match(valid_str, column_name, re.UNICODE)
    return is_valid


def get_searchable_columns(project_id):
    from pybossa.core import task_repo
    tasks = task_repo.filter_tasks_by(project_id=project_id,
                                      limit=1,
                                      desc=True)
    if not tasks:
        return []

    info = tasks[0].info
    if not isinstance(info, dict):
        return []

    columns = [key for key in info if is_valid_searchable_column(key)]
    return sorted(columns)


allowed_fields = {
    'task_id': 'id',
    'priority': 'priority_0',
    'finish_time': 'ft',
    'pcomplete': '(coalesce(ct, 0)/task.n_answers)',
    'created': 'task.created',
    'filter_by_field': 'filter_by_field'
}


def parse_tasks_browse_args(args):
    """
    Parse querystring arguments
    :param args: content of request.args
    :return: a dictionary of selected filters
    """
    parsed_args = dict()

    if args.get('task_id'):
        parsed_args["task_id"] = int(args['task_id'])
    if args.get('pcomplete_from'):
        parsed_args["pcomplete_from"] = float(args['pcomplete_from']) / 100
    if args.get('pcomplete_to'):
        parsed_args["pcomplete_to"] = float(args['pcomplete_to']) / 100
    if args.get('hide_completed'):
        parsed_args["hide_completed"] = args['hide_completed'].lower() == 'true'

    iso_string_format = '^\d{4}\-\d{2}\-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?$'

    if args.get('created_from'):
        if re.match(iso_string_format, args['created_from']):
            parsed_args["created_from"] = args['created_from']
        else:
            raise ValueError('created_from date format error, value: {}'
                             .format(args['created_from']))
    if args.get('created_to'):
        if re.match(iso_string_format, args['created_to']):
            parsed_args["created_to"] = args['created_to']
        else:
            raise ValueError('created_to date format error, value: {}'
                             .format(args['created_to']))
    if args.get('ftime_from'):
        if re.match(iso_string_format, args['ftime_from']):
            parsed_args["ftime_from"] = args['ftime_from']
        else:
            raise ValueError('ftime_from date format error, value: %s'
                             .format(args['ftime_from']))
    if args.get('ftime_to'):
        if re.match(iso_string_format, args['ftime_to']):
            parsed_args["ftime_to"] = args['ftime_to']
        else:
            raise ValueError('ftime_to date format error, value: %s'
                             .format(args['ftime_to']))
    if args.get('priority_from'):
        parsed_args["priority_from"] = float(args['priority_from'])
    if args.get('priority_to'):
        parsed_args["priority_to"] = float(args['priority_to'])
    if args.get('display_columns'):
        parsed_args["display_columns"] = json.loads(args['display_columns'])
    if not isinstance(parsed_args.get("display_columns"), list):
        parsed_args["display_columns"] = ['task_id', 'priority', 'pcomplete',
                                          'created', 'finish_time', 'actions']

    parsed_args["order_by_dict"] = dict()
    if args.get('order_by'):
        parsed_args["order_by"] = args['order_by'].strip().lower()
        for clause in parsed_args["order_by"].split(','):
            order_by_field = clause.split(' ')
            if len(order_by_field) != 2 or order_by_field[0] not in allowed_fields:
                raise ValueError('order_by value sent by the user is invalid: %s'.format(args['order_by']))
            if order_by_field[0] in parsed_args["order_by_dict"]:
                raise ValueError('order_by field is duplicated: %s'
                                 .format(args['order_by']))
            parsed_args["order_by_dict"][order_by_field[0]] = order_by_field[1]

        for key, value in allowed_fields.iteritems():
            parsed_args["order_by"] = parsed_args["order_by"].replace(key, value)

    if args.get('filter_by_field'):
        parsed_args['filter_by_field'] = _get_field_filters(args['filter_by_field'])

    return parsed_args


def _get_field_filters(filter_string):
    filters = json.loads(filter_string)
    return [(name, operator, value)
            for name, operator, value in filters
            if value and is_valid_searchable_column(name)]
