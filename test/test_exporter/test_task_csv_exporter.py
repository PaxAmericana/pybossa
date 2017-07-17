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
"""This module tests the TaskCsvExporter class."""

from default import Test, with_context
from pybossa.exporter.task_csv_export import TaskCsvExporter
from mock import patch
from codecs import encode


class TestTaskCsvExporter(Test):

    """Test PyBossa TaskCsvExporter module."""

    @with_context
    def test_task_csv_exporter_init(self):
        """Test that TaskCsvExporter init method works."""
        exporter = TaskCsvExporter()
        assert isinstance(exporter, TaskCsvExporter)

    @with_context
    def test_task_csv_exporter_get_keys(self):
        """Test that TaskCsvExporter get_keys method works."""
        exporter = TaskCsvExporter()

        row = {'a': {'nested_x': 'N'},
               'b': 1,
               'c': {
                 'nested_y': {'double_nested': 'www.example.com'},
                 'nested_z': True}}
        keys = sorted(exporter.get_keys(row, 'taskrun'))

        expected_keys = ['taskrun__a',
                         'taskrun__a__nested_x',
                         'taskrun__b',
                         'taskrun__c',
                         'taskrun__c__nested_y',
                         'taskrun__c__nested_y__double_nested',
                         'taskrun__c__nested_z']

        assert keys == expected_keys

    @with_context
    def test_task_csv_exporter_get_values(self):
        """Test that TaskCsvExporter get_values method works."""
        exporter = TaskCsvExporter()

        row = {'a': {'nested_x': 'N'},
               'b': 1,
               'c': {
                 'nested_y': {'double_nested': 'www.example.com'},
                 'nested_z': True}}

        value = exporter.get_value(row, *['c', 'nested_y', 'double_nested'])

        assert value == 'www.example.com'

        unicode_text = {'german': u'Straße auslösen zerstören',
                        'french': u'français américaine épais',
                        'chinese': u'中國的 英語 美國人',
                        'smart_quotes': u'“Hello”'}

        german_value = exporter.get_value(unicode_text, 'german')
        french_value = exporter.get_value(unicode_text, 'french')
        chinese_value = exporter.get_value(unicode_text, 'chinese')
        smart_quotes_value = exporter.get_value(unicode_text, 'smart_quotes')

        assert german_value == u'Stra\u00DFe ausl\u00F6sen zerst\u00F6ren'
        assert french_value == u'fran\u00E7ais am\u00E9ricaine \u00E9pais'
        assert chinese_value == u'\u4E2D\u570B\u7684 \u82F1\u8A9E \u7F8E\u570B\u4EBA'
        assert smart_quotes_value == u'\u201CHello\u201D'



