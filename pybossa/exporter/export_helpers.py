# -*- coding: utf8 -*-
# This file is part of PyBossa.
#
# Copyright (C) 2017 SciFabric LTD.
#
# PyBossa is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# PyBossa is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with PyBossa.  If not, see <http://www.gnu.org/licenses/>.
"""Exporter module helper functions."""
from sqlalchemy.sql import text
from pybossa.core import db
from pybossa.model.project import Project
from pybossa.cache.task_browse_helpers import get_task_filters


USER_FIELDS = [
    '"user".id         AS {}id',
    '"user".name       AS {}name',
    '"user".created    AS {}created',
    '"user".email_addr AS {}email_addr',
    '"user".fullname   AS {}fullname',
    '"user".user_pref  AS {}user_pref'
]

TASKRUN_FIELDS = [
    'task_run.id           AS {}id',
    'task_run.created      AS {}created',
    'task_run.project_id   AS {}project_id',
    'task_run.task_id      AS {}task_id',
    'task_run.user_id      AS {}user_id',
    'task_run.user_ip      AS {}user_ip',
    'task_run.finish_time  AS {}finish_time',
    'task_run.timeout      AS {}timeout',
    'task_run.calibration  AS {}calibration',
    'task_run.external_uid AS {}external_uid',
    'task_run.info         AS {}info'
]

TASK_FIELDS = [
    'task.id          AS {}id',
    'task.created     AS {}created',
    'task.project_id  AS {}project_id',
    'task.state       AS {}state',
    'task.quorum      AS {}quorum',
    'task.calibration AS {}calibration',
    'task.priority_0  AS {}priority_0',
    'task.info        AS {}info',
    'task.n_answers   AS {}n_answers',
    'task.exported    AS {}exported',
    'task.user_pref   AS {}user_pref'
]

session = db.slave_session


def _field_mapreducer(fields, prefix=''):
    return ',\n'.join(field.format(prefix) for field in fields)


def browse_tasks_export(obj, project_id, expanded, **kwargs):
    """Export tasks from the browse tasks view for a project
    using the same filters that are selected by the user
    in the UI.
    """
    filters, filter_params = get_task_filters(kwargs)
    if obj == 'task':
        sql = text('''
                   SELECT {0}
                     FROM task
                     LEFT OUTER JOIN (
                       SELECT task_id
                            , CAST(COUNT(id) AS FLOAT) AS ct
                            , MAX(finish_time) as ft
                         FROM task_run
                           WHERE project_id = :project_id
                           GROUP BY task_id
                       ) AS log_counts
                       ON task.id = log_counts.task_id
                     WHERE project_id = :project_id
                     {1}
                   '''.format(_field_mapreducer(TASK_FIELDS, ''),
                              filters)
                  )
    elif obj == 'task_run':
        if expanded:
           sql = text('''
                      SELECT {0}
                           , {1}
                           , {2}
                        FROM task_run
                        LEFT JOIN task
                          ON task_run.task_id = task.id
                        LEFT OUTER JOIN (
                          SELECT task_id
                               , CAST(COUNT(id) AS FLOAT) AS ct
                               , MAX(finish_time) as ft
                            FROM task_run
                              WHERE project_id = :project_id
                              GROUP BY task_id
                          ) AS log_counts
                          ON task.id = log_counts.task_id
                        LEFT JOIN "user"
                          ON task_run.user_id = "user".id
                        WHERE task_run.project_id = :project_id
                        {3}
                      '''.format(_field_mapreducer(TASKRUN_FIELDS, ''),
                                 _field_mapreducer(TASK_FIELDS, 'task__'),
                                 _field_mapreducer(USER_FIELDS, 'user__'),
                                 filters)
                     )
        else:
           sql = text('''
                      SELECT {0}
                        FROM task_run
                        LEFT JOIN task
                          ON task_run.task_id = task.id
                        LEFT OUTER JOIN (
                          SELECT task_id
                               , CAST(COUNT(id) AS FLOAT) AS ct
                               , MAX(finish_time) as ft
                            FROM task_run
                              WHERE project_id = :project_id
                              GROUP BY task_id
                          ) AS log_counts
                          ON task_run.task_id = log_counts.task_id
                        WHERE task_run.project_id = :project_id
                        {1}
                      '''.format(_field_mapreducer(TASKRUN_FIELDS, ''),
                                 filters)
                     )
    else:
        return

    return session.execute(sql, dict(project_id=project_id, **filter_params))


def browse_tasks_export_count(obj, project_id, expanded, **kwargs):
    """Returns the count of the tasks from the browse tasks view
    for a project using the same filters that are selected by
    the user in the UI.
    """
    filters, filter_params = get_task_filters(kwargs)
    if obj == 'task':
        sql = text('''
                   SELECT COUNT(task.id)
                     FROM task
                     LEFT OUTER JOIN (
                       SELECT task_id
                            , CAST(COUNT(id) AS FLOAT) AS ct
                            , MAX(finish_time) as ft
                         FROM task_run
                           WHERE project_id = :project_id
                           GROUP BY task_id
                       ) AS log_counts
                       ON task.id = log_counts.task_id
                     WHERE project_id = :project_id
                     {0}
                   '''.format(filters)
                  )
    elif obj == 'task_run':
       sql = text('''
                  SELECT COUNT(task_run.id)
                    FROM task_run
                    LEFT JOIN task
                      ON task_run.task_id = task.id
                    LEFT OUTER JOIN (
                      SELECT task_id
                           , CAST(COUNT(id) AS FLOAT) AS ct
                           , MAX(finish_time) as ft
                        FROM task_run
                          WHERE project_id = :project_id
                          GROUP BY task_id
                      ) AS log_counts
                      ON task.id = log_counts.task_id
                    WHERE task_run.project_id = :project_id
                    {0}
                  '''.format(filters)
                 )
    else:
        return

    return session.execute(sql, dict(project_id=project_id, **filter_params))
