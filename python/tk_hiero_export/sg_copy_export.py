"""Custom copy exporter that publishes copied offlines to ShotGrid.

Copyright (c) 2013 Shotgun Software Inc.

CONFIDENTIAL AND PROPRIETARY

This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit
Source Code License included in this distribution package. See LICENSE.
By accessing, using, copying or modifying this work you indicate your
agreement to the Shotgun Pipeline Toolkit Source Code License. All rights
not expressly granted therein are reserved by Shotgun Software Inc.
"""

import ast
import os

import sgtk
import tank
from hiero.exporters import FnCopyExporter, FnCopyExporterUI
from sgtk.platform.qt import QtGui, QtCore

from . import (
    HieroGetShot,
    HieroGetExtraPublishData,
    HieroUpdateVersionData,
    HieroPostVersionCreation,
)
from .base import ShotgunHieroObjectBase
from .collating_exporter import CollatingExporter, CollatedShotPreset
from .collating_exporter_ui import CollatingExporterUI


class ShotgunCopyExporterUI(ShotgunHieroObjectBase, FnCopyExporterUI.CopyExporterUI):
    """Preferences UI for the :class:`ShotgunCopyExporter`."""

    def __init__(self, preset):
        FnCopyExporterUI.CopyExporterUI.__init__(self, preset)
        CollatingExporterUI.__init__(self)
        self._displayName = "FPTR Copy Export"
        self._taskType = ShotgunCopyExporter

    def populateUI(self, widget, exportTemplate):
        FnCopyExporterUI.CopyExporterUI.populateUI(self, widget, exportTemplate)

        custom_widget = self._get_custom_widget(
            parent=widget,
            create_method="create_copy_exporter_widget",
            get_method="get_copy_exporter_ui_properties",
            set_method="set_copy_exporter_ui_properties",
        )
        if custom_widget is not None:
            layout = widget.layout()
            if layout is None:
                layout = QtGui.QVBoxLayout(widget)
            layout.addWidget(custom_widget)


class ShotgunCopyExporter(
    ShotgunHieroObjectBase, FnCopyExporter.CopyExporter, CollatingExporter
):
    """Exporter that copies media and registers a publish and version."""

    def __init__(self, initDict):
        FnCopyExporter.CopyExporter.__init__(self, initDict)
        CollatingExporter.__init__(self)
        self._resolved_export_path = None
        self._tk_version = None
        self._sg_shot = None
        self._sg_task = None
        self._extra_publish_data = None
        self._thumbnail = None

    def startTask(self):
        """Run Task"""
        if self._resolved_export_path is None:
            self._resolved_export_path = self.resolvedExportPath()
            self._tk_version = self._formatTkVersionString(self.versionString())

            # convert slashes to native os style..
            self._resolved_export_path = self._resolved_export_path.replace(
                "/", os.path.sep
            )

        # call the get_shot hook
        ########################
        if self.app.shot_count == 0:
            self.app.preprocess_data = {}

        # associate publishes with correct shot, which will be the hero item
        # if we are collating
        if self.isCollated() and not self.isHero():
            item = self.heroItem()
        else:
            item = self._item

        # store the shot for use in finishTask. query the head/tail values set
        # on the shot updater task so that we can set those values on the
        # Version created later.
        self._sg_shot = self.app.execute_hook(
            "hook_get_shot",
            task=self,
            item=item,
            data=self.app.preprocess_data,
            fields=["sg_head_in", "sg_tail_out"],
            base_class=HieroGetShot,
        )

        # populate the data dictionary for our Version while the item is still valid
        ##############################
        # see if we get a task to use
        self._sg_task = None
        try:
            task_filter = self.app.get_setting("default_task_filter", "[]")
            task_filter = ast.literal_eval(task_filter)
            task_filter.append(["entity", "is", self._sg_shot])
            tasks = self.app.shotgun.find("Task", task_filter)
            if len(tasks) == 1:
                self._sg_task = tasks[0]
        except ValueError:
            # continue without task
            setting = self.app.get_setting("default_task_filter", "[]")
            self.app.log_error("Invalid value for 'default_task_filter': %s" % setting)

        if self._preset.properties()["create_version"]:
            # lookup current login
            sg_current_user = tank.util.get_current_user(self.app.tank)

            file_name = os.path.basename(self._resolved_export_path)
            file_name = os.path.splitext(file_name)[0]

            # use the head/tail to populate frame first/last/range fields on
            # the Version
            head_in = self._sg_shot["sg_head_in"]
            tail_out = self._sg_shot["sg_tail_out"]

            self._version_data = {
                "user": sg_current_user,
                "created_by": sg_current_user,
                "entity": self._sg_shot,
                "project": self.app.context.project,
                "sg_path_to_movie": self._resolved_export_path,
                "code": file_name,
                "sg_first_frame": head_in,
                "sg_last_frame": tail_out,
                "frame_range": "%s-%s" % (head_in, tail_out),
            }

            if self._sg_task is not None:
                self._version_data["sg_task"] = self._sg_task

            # call the update version hook to allow for customization
            self.app.execute_hook(
                "hook_update_version_data",
                version_data=self._version_data,
                task=self,
                base_class=HieroUpdateVersionData,
            )

        # call the publish data hook to allow for publish customization
        self._extra_publish_data = self.app.execute_hook(
            "hook_get_extra_publish_data",
            task=self,
            base_class=HieroGetExtraPublishData,
        )

        # figure out the thumbnail frame
        ##########################
        # If we can't get a thumbnail it isn't the end of the world.
        # When we get to the upload we'll do nothing if we don't have
        # anything to work with, which will result in the same result
        # as if the thumbnail failed to upload.
        try:
            source = self._item.source()
            self._thumbnail = source.thumbnail(self._item.sourceIn())
        except Exception:
            self.app.log_error("Failed to generate thumbnail")
            pass

        return FnCopyExporter.CopyExporter.startTask(self)

    def finishTask(self):
        """Finish Task"""
        # run base class implementation
        FnCopyExporter.CopyExporter.finishTask(self)

        # create publish
        ################
        # by using entity instead of export path to get context, this ensures
        # collated plates get linked to the hero shot
        ctx = self.app.tank.context_from_entity("Shot", self._sg_shot["id"])
        published_file_type = self.app.get_setting("plate_published_file_type")

        args = {
            "tk": self.app.tank,
            "context": ctx,
            "path": self._resolved_export_path,
            "name": os.path.basename(self._resolved_export_path),
            "version_number": int(self._tk_version),
            "published_file_type": published_file_type,
        }

        if self._sg_task is not None:
            args["task"] = self._sg_task

        published_file_entity_type = sgtk.util.get_published_file_entity_type(
            self.app.sgtk
        )

        # register publish
        self.app.log_debug("Register publish in shotgun: %s" % str(args))
        pub_data = tank.util.register_publish(**args)
        if self._extra_publish_data is not None:
            self.app.log_debug(
                "Updating FPTR %s %s"
                % (published_file_entity_type, str(self._extra_publish_data))
            )
            self.app.shotgun.update(
                pub_data["type"], pub_data["id"], self._extra_publish_data
            )

        # upload thumbnail for publish
        if self._thumbnail:
            self._upload_thumbnail_to_sg(pub_data, self._thumbnail)
        else:
            self.app.log_debug(
                "There was no thumbnail available for %s %s"
                % (published_file_entity_type, str(self._extra_publish_data))
            )

        # create version
        ################
        vers = None
        if self._preset.properties()["create_version"]:
            if published_file_entity_type == "PublishedFile":
                self._version_data["published_files"] = [pub_data]
            else:  # == "TankPublishedFile
                self._version_data["tank_published_file"] = pub_data

            self.app.log_debug("Creating FPTR Version %s" % str(self._version_data))
            vers = self.app.shotgun.create("Version", self._version_data)

            self.app.log_debug(
                "Uploading quicktime to Flow Production Tracking... (%s)"
                % self._resolved_export_path
            )
            self.app.shotgun.upload(
                "Version", vers["id"], self._resolved_export_path, "sg_uploaded_movie"
            )

        # Post creation hook
        ####################
        if vers:
            self.app.execute_hook(
                "hook_post_version_creation",
                version_data=vers,
                base_class=HieroPostVersionCreation,
            )

        # Log usage metrics
        try:
            self.app.log_metric("Transcode & Publish", log_version=True)
        except:
            # ingore any errors. ex: metrics logging not supported
            pass


class ShotgunCopyPreset(
    ShotgunHieroObjectBase, FnCopyExporter.CopyPreset, CollatedShotPreset
):
    """Preset for the :class:`ShotgunCopyExporter`"""

    def __init__(self, name, properties):
        FnCopyExporter.CopyPreset.__init__(self, name, properties)
        self._parentType = ShotgunCopyExporter
        CollatedShotPreset.__init__(self, self.properties())

        # Handle custom properties from the customize_export_ui hook.
        custom_properties = (
            self._get_custom_properties("get_copy_exporter_ui_properties") or []
        )

        self.properties().update({d["name"]: d["value"] for d in custom_properties})
