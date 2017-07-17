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
# Cache global variables for timeouts

import json
from flask import url_for, safe_join, send_file, redirect
from pybossa.core import uploader, task_repo
from pybossa.uploader import local
from pybossa.exporter.json_export import JsonExporter
from export_helpers import browse_tasks_export, browse_tasks_export_count


class TaskJsonExporter(JsonExporter):
    """JSON Exporter for exporting ``Task``s and ``TaskRun``s
    for a project.
    """

    @staticmethod
    def merge_objects(t):
        """Merge joined objects into a single dictionary."""
        obj_dict = {}

        try:
            obj_dict = t.dictize()
        except:
            pass

        try:
            task = t.task.dictize()
            obj_dict['task'] = task
        except:
            pass

        try:
            user = t.user.dictize()
            allowed_attributes = ['name', 'fullname', 'created',
                                  'email_addr', 'admin', 'subadmin']
            user = {k: v for (k, v) in user.iteritems() if k in allowed_attributes}
            obj_dict['user'] = user
        except:
            pass

        return obj_dict

    @staticmethod
    def process_filtered_row(row):
        """Normalizes a row returned from a SQL query to
        the same format as that of merging joined domain
        objects.
        """
        def set_nested_value(row, keys, value):
            for key in keys[:-1]:
                row = row.setdefault(key, {})
            row[keys[-1]] = value

        new_row = {}
        for k, v in row.iteritems():
            key_split = k.split('__', 1)
            if len(key_split) > 1 and key_split[0] in ('task', 'user'):
                set_nested_value(new_row, key_split, v)
            else:
                new_row[k] = v

        return new_row

    def gen_json(self, obj, project_id, expanded=False):
        if obj == 'task':
            query_filter = task_repo.filter_tasks_by
        elif obj == 'task_run':
            query_filter = task_repo.filter_task_runs_by
        else:
            return

        n = getattr(task_repo, 'count_%ss_with' % obj)(project_id=project_id)
        sep = ", "
        yield "["

        for i, tr in enumerate(query_filter(project_id=project_id, yielded=True), 1):
            if expanded:
                item = self.merge_objects(tr)
            else:
                item = tr.dictize()

            item = json.dumps(item)

            if (i == n):
                sep = ""
            yield item + sep
        yield "]"

    def gen_json_with_filters(self, obj, project_id, expanded=False, **filters):
        objs = browse_tasks_export(obj, project_id, expanded, **filters)
        n = browse_tasks_export_count(obj, project_id, expanded, **filters)

        sep = ", "
        yield "["

        count = 0
        for obj in objs:
            item = json.dumps(self.process_filtered_row(dict(obj)))
            count += 1

            if (count == n):
                sep = ""
            yield item + sep
        yield "]"


    def _respond_json(self, ty, project_id, expanded=False, **filters):
        if filters:
            return self.gen_json_with_filters(
                    ty, project_id, expanded, **filters)
        else:
            return self.gen_json(ty, project_id, expanded)

    def response_zip(self, project, ty, expanded=False):
        return self.get_zip(project, ty, expanded)

    def get_zip(self, project, ty, expanded=False):
        """Delete existing ZIP file directly from uploads directory,
        generate one on the fly and upload it."""
        filename = self.download_name(project, ty)
        self.delete_existing_zip(project, ty)
        self._make_zip(project, ty, expanded)
        if isinstance(uploader, local.LocalUploader):
            filepath = self._download_path(project)
            res = send_file(filename_or_fp=safe_join(filepath, filename),
                            mimetype='application/octet-stream',
                            as_attachment=True,
                            attachment_filename=filename)
            # fail safe mode for more encoded filenames.
            # It seems Flask and Werkzeug do not support RFC 5987
            # http://greenbytes.de/tech/tc2231/#encoding-2231-char
            # res.headers['Content-Disposition'] = 'attachment; filename*=%s' % filename
            return res
        else:
            return redirect(url_for('rackspace', filename=filename,
                                    container=self._container(project),
                                    _external=True))

    def make_zip(self, project, obj, expanded=False, **filters):
        file_format = 'json'
        obj_generator = self._respond_json(obj, project.id, expanded, **filters)
        return self._make_zipfile(
                project, obj, file_format, obj_generator, expanded)

    def _make_zip(self, project, obj, expanded=False):
        self.make_zip(self, project, obj, expanded)
