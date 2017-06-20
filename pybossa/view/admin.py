# -* -coding: utf8 -*-
# This file is part of PYBOSSA.
#
# Copyright (C) 2017 Scifabric LTD.
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
"""Admin view for PYBOSSA."""
from rq import Queue
from flask import Blueprint
from flask import render_template
from flask import request
from flask import abort
from flask import flash
from flask import redirect
from flask import url_for
from flask import current_app
from flask import Response
from flask.ext.login import login_required, current_user
from flask.ext.babel import gettext
from flask_wtf.csrf import generate_csrf
from werkzeug.exceptions import HTTPException
from sqlalchemy.exc import ProgrammingError

from pybossa.model.category import Category
from pybossa.model.announcement import Announcement
from pybossa.util import admin_required, UnicodeWriter, handle_content_type
from pybossa.util import redirect_content_type
from pybossa.util import generate_invitation_email_for_admins_subadmins
from pybossa.util import generate_manage_user_email
from pybossa.cache import projects as cached_projects
from pybossa.cache import categories as cached_cat
from pybossa.cache import site_stats
from pybossa.auth import ensure_authorized_to
from pybossa.core import announcement_repo, project_repo, user_repo, sentinel
from pybossa.feed import get_update_feed
import pybossa.dashboard.data as dashb
from pybossa.jobs import get_dashboard_jobs
import json
from StringIO import StringIO

from pybossa.forms.admin_view_forms import *
from pybossa.news import NOTIFY_ADMIN
from pybossa.jobs import send_mail
from pybossa.core import userimporter
from pybossa.importers import BulkImportException
from pybossa.cache.users import get_users_for_report
from collections import OrderedDict


blueprint = Blueprint('admin', __name__)

DASHBOARD_QUEUE = Queue('super', connection=sentinel.master)

mail_queue = Queue('super', connection=sentinel.master)

MAX_NUM_USERS_IMPORT = 100

def format_error(msg, status_code):
    """Return error as a JSON response."""
    error = dict(error=msg,
                 status_code=status_code)
    return Response(json.dumps(error), status=status_code,
                    mimetype='application/json')


@blueprint.route('/')
@login_required
@admin_required
def index():
    """List admin actions."""
    key = NOTIFY_ADMIN + str(current_user.id)
    sentinel.master.delete(key)
    return handle_content_type(dict(template='/admin/index.html'))


@blueprint.route('/featured')
@blueprint.route('/featured/<int:project_id>', methods=['POST', 'DELETE'])
@login_required
@admin_required
def featured(project_id=None):
    """List featured projects of PYBOSSA."""
    try:
        if request.method == 'GET':
            categories = cached_cat.get_all()
            projects = {}
            for c in categories:
                n_projects = cached_projects.n_count(category=c.short_name)
                projects[c.short_name] = cached_projects.get(
                    category=c.short_name,
                    page=1,
                    per_page=n_projects)
            response = dict(template = '/admin/projects.html',
                            projects=projects,
                            categories=categories,
                            form=dict(csrf=generate_csrf()))
            return handle_content_type(response)
        else:
            project = project_repo.get(project_id)
            if project:
                ensure_authorized_to('update', project)
                if request.method == 'POST':
                    if project.featured is True:
                        msg = "Project.id %s already featured" % project_id
                        return format_error(msg, 415)
                    cached_projects.reset()
                    project.featured = True
                    project_repo.update(project)
                    return json.dumps(project.dictize())

                if request.method == 'DELETE':
                    if project.featured is False:
                        msg = 'Project.id %s is not featured' % project_id
                        return format_error(msg, 415)
                    cached_projects.reset()
                    project.featured = False
                    project_repo.update(project)
                    return json.dumps(project.dictize())
            else:
                msg = 'Project.id %s not found' % project_id
                return format_error(msg, 404)
    except Exception as e:  # pragma: no cover
        current_app.logger.error(e)
        return abort(500)


@blueprint.route('/users', methods=['GET', 'POST'])
@login_required
@admin_required
def users(user_id=None):
    """Manage users of PYBOSSA."""
    form = SearchForm(request.body)
    users = [user for user in user_repo.filter_by(admin=True)
             if user.id != current_user.id]

    if request.method == 'POST' and form.user.data:
        query = form.user.data
        found = [user for user in user_repo.search_by_name(query)
                 if user.id != current_user.id]
        [ensure_authorized_to('update', found_user) for found_user in found]
        if not found:
            flash("<strong>Ooops!</strong> We didn't find a user "
                  "matching your query: <strong>%s</strong>" % form.user.data)
        response = dict(template='/admin/users.html', found=found, users=users,
                        title=gettext("Manage Admin Users"),
                        form=form)
        return handle_content_type(response)

    response = dict(template='/admin/users.html', found=[], users=users,
                    title=gettext("Manage Admin Users"), form=form)
    return handle_content_type(response)


@blueprint.route('/users/export')
@login_required
@admin_required
def export_users():
    """Export Users list in the given format, only for admins."""
    exportable_attributes = ('id', 'name', 'fullname', 'email_addr',
                             'created', 'admin', 'subadmin', 'languages',
                             'locations', 'start_time', 'end_time',
                             'timezone', 'type_of_user', 'additional_comments',
                             'total_projects_contributed', 'completed_tasks',
                             'percentage_tasks_completed', 'first_submission_date',
                             'last_submission_date', 'avg_time_per_task'
                             )

    def respond_json():
        tmp = 'attachment; filename=all_users.json'
        res = Response(gen_json(), mimetype='application/json')
        res.headers['Content-Disposition'] = tmp
        return res

    def gen_json():
        users = get_users_for_report()
        jdata = json.dumps(users)
        return jdata

    def dictize_with_exportable_attributes(user):
        dict_user = {}
        for attr in exportable_attributes:
            dict_user[attr] = user[attr]
        return dict_user

    def respond_csv():
        out = StringIO()
        writer = UnicodeWriter(out)
        tmp = 'attachment; filename=all_users.csv'
        res = Response(gen_csv(out, writer, write_user), mimetype='text/csv')
        res.headers['Content-Disposition'] = tmp
        return res

    def gen_csv(out, writer, write_user):
        add_headers(writer)
        users = get_users_for_report()
        for user in users:
            write_user(writer, user)
        yield out.getvalue()

    def write_user(writer, user):
        values = [user[attr] for attr in exportable_attributes]
        writer.writerow(values)

    def add_headers(writer):
        writer.writerow(exportable_attributes)

    export_formats = ["json", "csv"]

    fmt = request.args.get('format')
    if not fmt:
        return redirect(url_for('.index'))
    if fmt not in export_formats:
        abort(415)
    return {"json": respond_json, "csv": respond_csv}[fmt]()


@blueprint.route('/users/add/<int:user_id>')
@login_required
@admin_required
def add_admin(user_id=None):
    """Add admin flag for user_id."""
    try:
        if user_id:
            user = user_repo.get(user_id)
            if user:
                ensure_authorized_to('update', user)
                user.admin = True
                user_repo.update(user)
                msg = generate_invitation_email_for_admins_subadmins(user, "Admin")
                if msg:
                    mail_queue.enqueue(send_mail, msg)
                return redirect_content_type(url_for(".users"))
            else:
                msg = "User not found"
                return format_error(msg, 404)
    except Exception as e:  # pragma: no cover
        current_app.logger.error(e)
        return abort(500)


@blueprint.route('/users/del/<int:user_id>')
@login_required
@admin_required
def del_admin(user_id=None):
    """Del admin flag for user_id."""
    try:
        if user_id:
            user = user_repo.get(user_id)
            if user:
                ensure_authorized_to('update', user)
                user.admin = False
                user_repo.update(user)
                return redirect_content_type(url_for('.users'))
            else:
                msg = "User.id not found"
                return format_error(msg, 404)
        else:  # pragma: no cover
            msg = "User.id is missing for method del_admin"
            return format_error(msg, 415)
    except Exception as e:  # pragma: no cover
        current_app.logger.error(e)
        return abort(500)


@blueprint.route('/categories', methods=['GET', 'POST'])
@login_required
@admin_required
def categories():
    """List Categories."""
    try:
        if request.method == 'GET':
            ensure_authorized_to('read', Category)
            form = CategoryForm()
        if request.method == 'POST':
            ensure_authorized_to('create', Category)
            form = CategoryForm(request.body)
            del form.id
            if form.validate():
                slug = form.name.data.lower().replace(" ", "")
                category = Category(name=form.name.data,
                                    short_name=slug,
                                    description=form.description.data)
                project_repo.save_category(category)
                cached_cat.reset()
                msg = gettext("Category added")
                flash(msg, 'success')
            else:
                flash(gettext('Please correct the errors'), 'error')
        categories = cached_cat.get_all()
        n_projects_per_category = dict()
        for c in categories:
            n_projects_per_category[c.short_name] = \
                cached_projects.n_count(c.short_name)

        response = dict(template='admin/categories.html',
                        title=gettext('Categories'),
                        categories=categories,
                        n_projects_per_category=n_projects_per_category,
                        form=form)
        return handle_content_type(response)
    except Exception as e:  # pragma: no cover
        current_app.logger.error(e)
        return abort(500)


@blueprint.route('/categories/del/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def del_category(id):
    """Delete a category."""
    try:
        category = project_repo.get_category(id)
        if category:
            if len(cached_cat.get_all()) > 1:
                ensure_authorized_to('delete', category)
                if request.method == 'GET':
                    response = dict(template='admin/del_category.html',
                                    title=gettext('Delete Category'),
                                    category=category,
                                    form=dict(csrf=generate_csrf()))
                    return handle_content_type(response)
                if request.method == 'POST':
                    project_repo.delete_category(category)
                    msg = gettext("Category deleted")
                    flash(msg, 'success')
                    cached_cat.reset()
                    return redirect_content_type(url_for(".categories"))
            else:
                msg = gettext('Sorry, it is not possible to delete the only'
                              ' available category. You can modify it, '
                              ' click the edit button')
                flash(msg, 'warning')
                return redirect_content_type(url_for('.categories'))
        else:
            abort(404)
    except HTTPException:
        raise
    except Exception as e:  # pragma: no cover
        current_app.logger.error(e)
        return abort(500)


@blueprint.route('/categories/update/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def update_category(id):
    """Update a category."""
    try:
        category = project_repo.get_category(id)
        if category:
            ensure_authorized_to('update', category)
            form = CategoryForm(obj=category)
            form.populate_obj(category)
            if request.method == 'GET':
                response = dict(template='admin/update_category.html',
                                title=gettext('Update Category'),
                                category=category,
                                form=form)
                return handle_content_type(response)
            if request.method == 'POST':
                form = CategoryForm(request.body)
                if form.validate():
                    slug = form.name.data.lower().replace(" ", "")
                    new_category = Category(id=form.id.data,
                                            name=form.name.data,
                                            short_name=slug)
                    project_repo.update_category(new_category)
                    cached_cat.reset()
                    msg = gettext("Category updated")
                    flash(msg, 'success')
                    return redirect_content_type(url_for(".categories"))
                else:
                    msg = gettext("Please correct the errors")
                    flash(msg, 'success')
                    response = dict(template='admin/update_category.html',
                                    title=gettext('Update Category'),
                                    category=category,
                                    form=form)
                    return handle_content_type(response)
        else:
            abort(404)
    except HTTPException:
        raise
    except Exception as e:  # pragma: no cover
        current_app.logger.error(e)
        return abort(500)


@blueprint.route('/announcement', methods=['GET'])
@login_required
@admin_required
def announcement():
    """Manage anncounements."""
    announcements = announcement_repo.get_all_announcements()
    response = dict(template='', # template='admin/announcement.html',
                    title=gettext("Manage global Announcements"),
                    announcements=announcements,
                    csrf=generate_csrf())
    return handle_content_type(response)


@blueprint.route('/announcement/new', methods=['GET', 'POST'])
@login_required
@admin_required
def new_announcement():
    """Create new announcement."""
    def respond():
        response = dict(template='', # template='admin/new_announcement.html',
                        title=gettext("Write a new post"),
                        form=form)
        return handle_content_type(response)

    form = AnnouncementForm()
    del form.id

    # project_sanitized, owner_sanitized = sanitize_project_owner(project, owner, current_user)

    if request.method != 'POST':
        ensure_authorized_to('create', Announcement())
        return respond()

    if not form.validate():
        flash(gettext('Please correct the errors'), 'error')
        return respond()

    announcement = Announcement(title=form.title.data,
                                body=form.body.data,
                                user_id=current_user.id)
    ensure_authorized_to('create', announcement)
    announcement_repo.save(announcement)

    msg_1 = gettext('Annnouncement created!')
    flash('<i class="icon-ok"></i> ' + msg_1, 'success')

    return redirect_content_type(url_for('admin.announcement'))


@blueprint.route('/announcement/<int:id>/update', methods=['GET', 'POST'])
@login_required
@admin_required
def update_announcement(id):
    announcement = announcement_repo.get_by(id=id)
    if announcement is None:
        raise abort(404)

    def respond():
        response = dict(template='',  # template='admin/update_announcement.html',
                        title=gettext("Edit a post"),
                        form=form)
        return handle_content_type(response)

    form = AnnouncementForm()

    if request.method != 'POST':
        ensure_authorized_to('update', announcement)
        form = AnnouncementForm(obj=announcement)
        return respond()

    if not form.validate():
        flash(gettext('Please correct the errors'), 'error')
        return respond()

    ensure_authorized_to('update', announcement)
    announcement = Announcement(id=form.id.data,
                                title=form.title.data,
                                body=form.body.data,
                                user_id=current_user.id)
    announcement_repo.update(announcement)

    msg_1 = gettext('Announcement updated!')
    flash('<i class="icon-ok"></i> ' + msg_1, 'success')

    return redirect_content_type(url_for('admin.announcement'))


@blueprint.route('/announcement/<int:id>/delete', methods=['POST'])
@login_required
@admin_required
def delete_announcement(id):
    announcement = announcement_repo.get_by(id=id)
    if announcement is None:
        raise abort(404)

    ensure_authorized_to('delete', announcement)
    announcement_repo.delete(announcement)
    flash('<i class="icon-ok"></i> ' + 'Announcement deleted!', 'success')
    return redirect_content_type(url_for('admin.announcement'))


@blueprint.route('/dashboard/')
@login_required
@admin_required
def dashboard():
    """Show PYBOSSA Dashboard."""
    try:
        if request.args.get('refresh') == '1':
            db_jobs = get_dashboard_jobs()
            for j in db_jobs:
                DASHBOARD_QUEUE.enqueue(j['name'])
            msg = gettext('Dashboard jobs enqueued,'
                          ' refresh page in a few minutes')
            flash(msg)
        active_users_last_week = dashb.format_users_week()
        active_anon_last_week = dashb.format_anon_week()
        draft_projects_last_week = dashb.format_draft_projects()
        published_projects_last_week = dashb.format_published_projects()
        update_projects_last_week = dashb.format_update_projects()
        new_tasks_week = dashb.format_new_tasks()
        new_task_runs_week = dashb.format_new_task_runs()
        new_users_week = dashb.format_new_users()
        returning_users_week = dashb.format_returning_users()
        update_feed = get_update_feed()

        response = dict(
            template='admin/dashboard.html',
            title=gettext('Dashboard'),
            active_users_last_week=active_users_last_week,
            active_anon_last_week=active_anon_last_week,
            draft_projects_last_week=draft_projects_last_week,
            published_projects_last_week=published_projects_last_week,
            update_projects_last_week=update_projects_last_week,
            new_tasks_week=new_tasks_week,
            new_task_runs_week=new_task_runs_week,
            new_users_week=new_users_week,
            returning_users_week=returning_users_week,
            update_feed=update_feed,
            wait=False)
        return handle_content_type(response)
    except ProgrammingError as e:
        response = dict(template='admin/dashboard.html',
                        title=gettext('Dashboard'),
                        wait=True)
        return handle_content_type(response)
    except Exception as e:  # pragma: no cover
        current_app.logger.error(e)
        return abort(500)


@blueprint.route('/management_dashboard/')
@login_required
@admin_required
def management_dashboard():
    project_chart = site_stats.project_chart()
    category_chart = site_stats.category_chart()
    task_chart = site_stats.task_chart()
    submission_chart = site_stats.submission_chart()
    current_app.logger.info(task_chart)

    timed_stats_funcs = [
        site_stats.number_of_active_jobs,
        site_stats.number_of_created_jobs,
        site_stats.number_of_created_tasks,
        site_stats.number_of_completed_tasks,
        site_stats.avg_time_to_complete_task,
        site_stats.number_of_active_users,
        site_stats.categories_with_new_projects
    ]

    current_stats_funcs = [
        site_stats.avg_task_per_job,
        site_stats.tasks_per_category
    ]

    timed_stats = OrderedDict()
    for func in timed_stats_funcs:
        timed_stats[func.__doc__] = OrderedDict()
        for days in [30, 60, 90, 350, 'all']:
            timed_stats[func.__doc__][days] = func(days)

    current_stats = OrderedDict((func.__doc__, func())
                                for func in current_stats_funcs)

    return render_template('admin/management_dashboard.html',
                           timed_stats=timed_stats,
                           current_stats=current_stats,
                           project_chart=project_chart,
                           category_chart=category_chart,
                           task_chart=task_chart,
                           submission_chart=submission_chart)


@blueprint.route('/subadminusers', methods=['GET', 'POST'])
@login_required
@admin_required
def subadminusers(user_id=None):
    """Manage subadminusers of PyBossa."""
    form = SearchForm(request.form)
    users = [user for user in user_repo.filter_by(subadmin=True)
             if user.id != current_user.id]

    if request.method == 'POST' and form.user.data:
        query = form.user.data
        found = [user for user in user_repo.search_by_name(query)
                 if user.id != current_user.id]
        [ensure_authorized_to('update', found_user) for found_user in found]
        if not found:
            flash("<strong>Ooops!</strong> We didn't find a user "
                  "matching your query: <strong>%s</strong>" % form.user.data)
        return render_template('/admin/subadminusers.html', found=found, users=users,
                               title=gettext("Manage Subadmin Users"),
                               form=form)

    return render_template('/admin/subadminusers.html', found=[], users=users,
                           title=gettext("Manage Subadmin Users"), form=form)


@blueprint.route('/users/addsubadmin/<int:user_id>')
@login_required
@admin_required
def add_subadmin(user_id=None):
    """Add subadmin flag for user_id."""
    try:
        if user_id:
            user = user_repo.get(user_id)
            if user:
                ensure_authorized_to('update', user)
                user.subadmin = True
                user_repo.update(user)
                msg = generate_invitation_email_for_admins_subadmins(user, "Subadmin")
                if msg:
                    mail_queue.enqueue(send_mail, msg)
                return redirect(url_for(".subadminusers"))
            else:
                msg = "User not found"
                return format_error(msg, 404)
    except Exception as e:  # pragma: no cover
        current_app.logger.error(e)
        return abort(500)


@blueprint.route('/users/delsubadmin/<int:user_id>')
@login_required
@admin_required
def del_subadmin(user_id=None):
    """Del admin flag for user_id."""
    try:
        if user_id:
            user = user_repo.get(user_id)
            if user:
                ensure_authorized_to('update', user)
                user.subadmin = False
                user_repo.update(user)
                return redirect(url_for('.subadminusers'))
            else:
                msg = "User.id not found"
                return format_error(msg, 404)
        else:  # pragma: no cover
            msg = "User.id is missing for method del_admin"
            return format_error(msg, 415)
    except Exception as e:  # pragma: no cover
        current_app.logger.error(e)
        return abort(500)


def _import_users(**form_data):
    number_of_users = userimporter.count_users_to_import(**form_data)

    if number_of_users <= MAX_NUM_USERS_IMPORT:
        message = userimporter.create_users(user_repo, **form_data)
        flash(message, 'success')
    else:
        message = "Maximum number of users that can be imported are {0}".format(MAX_NUM_USERS_IMPORT)
        flash(message, 'error')
    return redirect(url_for('.index'))


@blueprint.route('/userimport', methods=['GET', 'POST'])
@login_required
@admin_required
def userimport():
    """Import Users in bulk using local csv containing user information; only for admins."""
    importer_type = request.form.get('form_name') or request.args.get('type')
    all_importers = userimporter.get_all_importer_names()
    if importer_type is not None and importer_type not in all_importers:
        raise abort(404)
    form = GenericUserImportForm()(importer_type, request.form)

    if request.method == 'POST':
        if form.validate():
            if 'file' not in request.files:
                flash(msg, 'No file part')
            try:
                return _import_users(**form.get_import_data())
            except BulkImportException as err_msg:
                flash(gettext(str(err_msg)), 'error')
            except Exception:  # pragma: no cover
                current_app.logger.exception('Error in userimport')
                msg = 'Oops! Looks like there was an error!'
                flash(gettext(msg), 'error')
                return abort(500)
        else:
            flash(gettext('Please correct the errors'), 'error')
    return render_template('/admin/userimport.html', form=form)


@blueprint.route('/manageusers', methods=['GET', 'POST'])
@login_required
@admin_required
def manageusers(user_id=None):
    """Enable/disable users of PyBossa."""
    form = SearchForm(request.form)
    users = [user for user in user_repo.filter_by(enabled=True)
             if user.id != current_user.id]
    disabledusers = [user for user in user_repo.filter_by(enabled=False)
             if user.id != current_user.id]

    if request.method == 'POST' and form.user.data:
        query = form.user.data
        found = [user for user in user_repo.search_by_name(query)
                 if user.id != current_user.id]
        [ensure_authorized_to('update', found_user) for found_user in found]
        if not found:
            flash("<strong>Ooops!</strong> We didn't find a user "
                  "matching your query: <strong>%s</strong>" % form.user.data)
        return render_template('/admin/manageusers.html', found=found, users=users,
                               disabledusers=disabledusers, title=gettext("Enable/Disable Users"),
                               form=form)

    return render_template('/admin/manageusers.html', found=[], users=users,
                           disabledusers=disabledusers, title=gettext("Enable/Disable Users"), form=form)


@blueprint.route('/users/enable_user/<int:user_id>')
@login_required
@admin_required
def enable_user(user_id=None):
    """Set enabled flag to True for user_id."""
    try:
        if user_id:
            user = user_repo.get(user_id)
            if user:
                ensure_authorized_to('update', user)
                user.enabled = True
                user_repo.update(user)
                msg = generate_manage_user_email(user, "enable")
                if msg:
                    mail_queue.enqueue(send_mail, msg)
                return redirect(url_for(".manageusers"))
        msg = "User not found"
        return format_error(msg, 404)
    except Exception as e:  # pragma: no cover
        current_app.logger.error(e)
        return abort(500)


@blueprint.route('/users/disable_user/<int:user_id>')
@login_required
@admin_required
def disable_user(user_id=None):
    """Set enabled flag to False for user_id."""
    try:
        if user_id:
            user = user_repo.get(user_id)
            if user:
                ensure_authorized_to('update', user)
                user.enabled = False
                user_repo.update(user)
                msg = generate_manage_user_email(user, "disable")
                if msg:
                    mail_queue.enqueue(send_mail, msg)
                return redirect(url_for('.manageusers'))
        msg = "User not found"
        return format_error(msg, 404)
    except Exception as e:  # pragma: no cover
        current_app.logger.error(e)
        return abort(500)
