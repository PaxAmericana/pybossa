# -*- coding: utf8 -*-
# This file is part of PYBOSSA.
#
# Copyright (C) 2015 Scifabric LTD.
#
# PYBOSSA is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# PYBOSSA is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with PYBOSSA.  If not, see <http://www.gnu.org/licenses/>.
"""
PYBOSSA api module for exposing domain objects via an API.

This package adds GET, POST, PUT and DELETE methods for:
    * projects,
    * categories,
    * tasks,
    * task_runs,
    * users,
    * global_stats,
    * vmcp
    * completedtasks
    * completedtaskruns

"""

import json
import jwt
from flask import Blueprint, request, abort, Response, make_response
from flask.ext.login import current_user
from werkzeug.exceptions import NotFound
from pybossa.util import jsonpify, get_user_id_or_ip, fuzzyboolean
from pybossa.util import get_disqus_sso_payload
import pybossa.model as model
from pybossa.core import csrf, ratelimits, sentinel
from pybossa.ratelimit import ratelimit
from pybossa.cache.projects import n_tasks
import pybossa.sched as sched
from pybossa.error import ErrorStatus
from global_stats import GlobalStatsAPI
from task import TaskAPI
from task_run import TaskRunAPI
from project import ProjectAPI
from announcement import AnnouncementAPI
from blogpost import BlogpostAPI
from category import CategoryAPI
from vmcp import VmcpAPI
from favorites import FavoritesAPI
from user import UserAPI
from token import TokenAPI
from result import ResultAPI
from helpingmaterial import HelpingMaterialAPI
from pybossa.core import project_repo, task_repo
from pybossa.contributions_guard import ContributionsGuard
from pybossa.auth import jwt_authorize_project
from werkzeug.exceptions import MethodNotAllowed
from completed_task import CompletedTaskAPI
from completed_task_run import CompletedTaskRunAPI

blueprint = Blueprint('api', __name__)

error = ErrorStatus()


@blueprint.route('/')
@ratelimit(limit=ratelimits.get('LIMIT'), per=ratelimits.get('PER'))
def index():  # pragma: no cover
    """Return dummy text for welcome page."""
    return 'The PYBOSSA API'


def register_api(view, endpoint, url, pk='id', pk_type='int'):
    """Register API endpoints.

    Registers new end points for the API using classes.

    """
    view_func = view.as_view(endpoint)
    csrf.exempt(view_func)
    blueprint.add_url_rule(url,
                           view_func=view_func,
                           defaults={pk: None},
                           methods=['GET', 'OPTIONS'])
    blueprint.add_url_rule(url,
                           view_func=view_func,
                           methods=['POST', 'OPTIONS'])
    blueprint.add_url_rule('%s/<%s:%s>' % (url, pk_type, pk),
                           view_func=view_func,
                           methods=['GET', 'PUT', 'DELETE', 'OPTIONS'])

register_api(ProjectAPI, 'api_project', '/project', pk='oid', pk_type='int')
register_api(CategoryAPI, 'api_category', '/category', pk='oid', pk_type='int')
register_api(TaskAPI, 'api_task', '/task', pk='oid', pk_type='int')
register_api(TaskRunAPI, 'api_taskrun', '/taskrun', pk='oid', pk_type='int')
register_api(ResultAPI, 'api_result', '/result', pk='oid', pk_type='int')
register_api(UserAPI, 'api_user', '/user', pk='oid', pk_type='int')
register_api(AnnouncementAPI, 'api_announcement', '/announcement', pk='oid', pk_type='int')
register_api(BlogpostAPI, 'api_blogpost', '/blogpost', pk='oid', pk_type='int')
register_api(HelpingMaterialAPI, 'api_helpingmaterial',
             '/helpingmaterial', pk='oid', pk_type='int')
register_api(GlobalStatsAPI, 'api_globalstats', '/globalstats',
             pk='oid', pk_type='int')
register_api(VmcpAPI, 'api_vmcp', '/vmcp', pk='oid', pk_type='int')
register_api(FavoritesAPI, 'api_favorites', '/favorites',
             pk='oid', pk_type='int')
register_api(TokenAPI, 'api_token', '/token', pk='token', pk_type='string')
register_api(CompletedTaskAPI, 'api_completedtask', '/completedtask', pk='oid', pk_type='int')
register_api(CompletedTaskRunAPI, 'api_completedtaskrun', '/completedtaskrun', pk='oid', pk_type='int')

@jsonpify
@blueprint.route('/project/<project_id>/newtask')
@ratelimit(limit=ratelimits.get('LIMIT'), per=ratelimits.get('PER'))
def new_task(project_id):
    """Return a new task for a project."""
    # Check if the request has an arg:
    try:
        tasks = _retrieve_new_task(project_id)

        if type(tasks) is Response:
            return tasks

        # If there is a task for the user, return it
        if tasks is not None:
            guard = ContributionsGuard(sentinel.master)
            for task in tasks:
                guard.stamp(task, get_user_id_or_ip())
            data = [task.dictize() for task in tasks]
            if len(data) == 0:
                response = make_response(json.dumps({}))
            elif len(data) == 1:
                response = make_response(json.dumps(data[0]))
            else:
                response = make_response(json.dumps(data))
            response.mimetype = "application/json"
            return response
        return Response(json.dumps({}), mimetype="application/json")
    except Exception as e:
        return error.format_exception(e, target='project', action='GET')


def _retrieve_new_task(project_id):

    project = project_repo.get(project_id)

    if project is None:
        raise NotFound

    if not project.allow_anonymous_contributors and current_user.is_anonymous():
        info = dict(
            error="This project does not allow anonymous contributors")
        error = [model.task.Task(info=info)]
        return error

    if request.args.get('external_uid'):
        resp = jwt_authorize_project(project,
                                     request.headers.get('Authorization'))
        if resp != True:
            return resp

    if request.args.get('limit'):
        limit = int(request.args.get('limit'))
    else:
        limit = 1

    if limit > 100:
        limit = 100

    if request.args.get('offset'):
        offset = int(request.args.get('offset'))
    else:
        offset = 0

    if request.args.get('orderby'):
        orderby = request.args.get('orderby')
    else:
        orderby = 'id'

    if request.args.get('desc'):
        desc = fuzzyboolean(request.args.get('desc'))
    else:
        desc = False

    user_id = None if current_user.is_anonymous() else current_user.id
    user_ip = request.remote_addr if current_user.is_anonymous() else None
    external_uid = request.args.get('external_uid')
    task = sched.new_task(project_id, project.info.get('sched'),
                          user_id,
                          user_ip,
                          external_uid,
                          offset,
                          limit,
                          orderby=orderby,
                          desc=desc)
    return task


@jsonpify
@blueprint.route('/app/<task_id>/cachePresentedTime')
@blueprint.route('/task/<task_id>/cachePresentedTime')
@crossdomain(origin='*', headers=cors_headers)
@ratelimit(limit=ratelimits.get('LIMIT'), per=ratelimits.get('PER'))
def cache_presented_time(task_id):
    """Cache presented time to Redis."""

    # 'force' set to False since we do not want to overwrite
    # the presented time on browser reloads or accidental logouts.
    set_cache_presented_time(task_id, force=False)
    return Response(json.dumps({}), mimetype="application/json")

def set_cache_presented_time(task_id, force=False):
    """Cache in Redis the initial datetime that a task was presented to a user.
    If force=True, cache will be updated if there is no key or an existing key.
    If force=False, cache will only be updated if no key exists.
    """
    guard = ContributionsGuard(sentinel.master)

    # usr can only be a registered user with a user_id
    usr = get_user_id_or_ip()['user_id'] or None

    # Only set cache if usr is not None so that the cache cannot be set by calling the API directly.
    # Set presented_time value if presented_time_key does not exist yet.
    # The presented time cannot be reset until it times out. 
    # This will eliminate the ability for someone to manipulate the presented time
    # to make it look like they spent less time on a task than they actually did.
    # Besides user manipulation, if guards against browser reloads and accidental logouts.
    # This is an appropriate solution since we do not have complete information
    # regarding whether or not a user actually looked at a task before a browser reload,
    # logout or timeout.
    if usr is not None and not guard.check_task_presented_timestamp(task_id, get_user_id_or_ip()):
        guard.stamp_presented_time(task_id, get_user_id_or_ip())

    # Only overwrite an existing presented_time_value if force = True.
    # This should ONLY be used if there is no way for a user to take advantage
    # of this feature to manuipulate the presented time.
    elif force == True:
        guard.stamp_presented_time(task_id, get_user_id_or_ip())


@jsonpify
@blueprint.route('/app/<short_name>/userprogress')
@blueprint.route('/project/<short_name>/userprogress')
@blueprint.route('/app/<int:project_id>/userprogress')
@blueprint.route('/project/<int:project_id>/userprogress')
@ratelimit(limit=ratelimits.get('LIMIT'), per=ratelimits.get('PER'))
def user_progress(project_id=None, short_name=None):
    """API endpoint for user progress.

    Return a JSON object with two fields regarding the tasks for the user:
        { 'done': 10,
          'total: 100
        }
       This will mean that the user has done a 10% of the available tasks for
       him

    """
    if project_id or short_name:
        if short_name:
            project = project_repo.get_by_shortname(short_name)
        elif project_id:
            project = project_repo.get(project_id)

        if project:
            # For now, keep this version, but wait until redis cache is used here for task_runs too
            query_attrs = dict(project_id=project.id)
            if current_user.is_anonymous():
                query_attrs['user_ip'] = request.remote_addr or '127.0.0.1'
            else:
                query_attrs['user_id'] = current_user.id
            taskrun_count = task_repo.count_task_runs_with(**query_attrs)
            tmp = dict(done=taskrun_count, total=n_tasks(project.id))
            return Response(json.dumps(tmp), mimetype="application/json")
        else:
            return abort(404)
    else:  # pragma: no cover
        return abort(404)


@jsonpify
@blueprint.route('/auth/project/<short_name>/token')
@ratelimit(limit=ratelimits.get('LIMIT'), per=ratelimits.get('PER'))
def auth_jwt_project(short_name):
    """Create a JWT for a project via its secret KEY."""
    project_secret_key = None
    if 'Authorization' in request.headers:
        project_secret_key = request.headers.get('Authorization')
    if project_secret_key:
        project = project_repo.get_by_shortname(short_name)
        if project and project.secret_key == project_secret_key:
            token = jwt.encode({'short_name': short_name,
                                'project_id': project.id},
                               project.secret_key, algorithm='HS256')
            return token
        else:
            return abort(404)
    else:
        return abort(403)


@jsonpify
@blueprint.route('/disqus/sso')
@ratelimit(limit=ratelimits.get('LIMIT'), per=ratelimits.get('PER'))
def get_disqus_sso_api():
    """Return remote_auth_s3 and api_key for disqus SSO."""
    try:
        if current_user.is_authenticated():
            message, timestamp, sig, pub_key = get_disqus_sso_payload(current_user)
        else:
            message, timestamp, sig, pub_key = get_disqus_sso_payload(None)

        if message and timestamp and sig and pub_key:
            remote_auth_s3 = "%s %s %s" % (message, sig, timestamp)
            tmp = dict(remote_auth_s3=remote_auth_s3, api_key=pub_key)
            return Response(json.dumps(tmp), mimetype='application/json')
        else:
            raise MethodNotAllowed
    except MethodNotAllowed as e:
        e.message = "Disqus keys are missing"
        return error.format_exception(e, target='DISQUS_SSO', action='GET')
