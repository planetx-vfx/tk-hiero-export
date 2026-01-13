# Copyright (c) 2014 Shotgun Software Inc.
#
# CONFIDENTIAL AND PROPRIETARY
#
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights
# not expressly granted therein are reserved by Shotgun Software Inc.
import ast

from tank import Hook


class HieroResolveCustomStrings(Hook):
    """
    This class implements a hook that is used to resolve custom tokens into
    their concrete value when paths are being processed during the export.
    """

    # Cache of shots that have already been pulled from shotgun
    _sg_lookup_cache = {}

    def execute(self, task, keyword, **kwargs):
        """
        The default implementation of the custom resolver simply looks up
        the keyword from the Shotgun Shot entity dictionary. For example,
        to pull the shot code, you would simply specify 'code'. To pull
        the sequence code you would use 'sg_sequence.Sequence.code'.

        :param task: The export task being processed.
        :param str keyword: The keyword token that needs to be resolved.

        :returns: The resolved keyword value to be replaced into the
            associated string.
        :rtype: str
        """
        shot_code = task._item.name()

        # grab the shot from the cache, or the get_shot hook if not cached
        sg_shot = self._sg_lookup_cache.get(shot_code)

        # Replace the keyword {prj} so the lookup will work correctly on a shot.
        keyword = keyword.replace("{prj}", "{project.Project.sg_short_name}")
        if sg_shot is None:
            fields = [
                ctf["keyword"].replace("prj", "project.Project.sg_short_name")
                for ctf in self.parent.get_setting("custom_template_fields")
            ]
            sg_shot = self.parent.execute_hook(
                "hook_get_shot",
                task=task,
                item=task._item,
                data=self.parent.preprocess_data,
                fields=fields,
                upload_thumbnail=False,
            )

            self._sg_lookup_cache[shot_code] = sg_shot

        self.parent.log_info("_sg_lookup_cache: %s" % self._sg_lookup_cache)

        if sg_shot is None:
            raise RuntimeError("Could not find shot for custom resolver: %s" % keyword)

        # strip off the leading and trailing curly brackets
        keyword = keyword[1:-1]
        result = sg_shot.get(keyword, "")

        if keyword == "Episode":
            episode_entity = self.parent.execute_hook_method(
                "hook_get_shot",
                "get_episode",
                data=self.parent.preprocess_data,
                hiero_sequence=task._item.parentSequence(),
            )
            result = episode_entity["code"]

        if keyword == "Step" or keyword == "Task":
            task_filter = self.parent.get_setting("default_task_filter", "[]")
            task_filter = ast.literal_eval(task_filter)
            task_filter.append(["entity", "is", sg_shot])
            tasks = self.parent.shotgun.find(
                "Task", task_filter, ["content", "step.Step.short_name"]
            )
            self.parent.log_info("Found tasks: %s" % tasks)
            if len(tasks) >= 1:
                if keyword == "Step":
                    result = tasks[0].get("step.Step.short_name", "")
                if keyword == "Task":
                    result = tasks[0].get("content", "")

        self.parent.log_debug(
            'Custom resolver: %s[%s] -> "%s"' % (shot_code, keyword, result)
        )

        return result
