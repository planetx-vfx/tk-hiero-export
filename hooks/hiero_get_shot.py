# Copyright (c) 2013 Shotgun Software Inc.
#
# CONFIDENTIAL AND PROPRIETARY
#
# This work is provided "AS IS" and subject to the Shotgun Pipeline Toolkit
# Source Code License included in this distribution package. See LICENSE.
# By accessing, using, copying or modifying this work you indicate your
# agreement to the Shotgun Pipeline Toolkit Source Code License. All rights
# not expressly granted therein are reserved by Shotgun Software Inc.

from tank import Hook


class HieroGetShot(Hook):
    """
    Return a Flow Production Tracking Shot dictionary for the given Hiero items
    """

    def execute(self, task, item, data, **kwargs):
        """
        Takes a hiero.core.TrackItem as input and returns a data dictionary for
        the shot to update the cut info for.

        :param task: The Hiero task being processed.
        :param item: The hiero.core.TrackItem being processed.
        :param dict data: A dictionary with cached parent data.

        :returns: A Shot entity.
        :rtype: dict
        """
        # get the parent entity for the Shot
        parent = self.get_shot_parent(item.parentSequence(), data, item=item)

        # shot parent field
        parent_field = "sg_sequence"

        # grab shot from Flow Production Tracking
        sg = self.parent.shotgun
        filter = [
            ["project", "is", self.parent.context.project],
            [parent_field, "is", parent],
            ["code", "is", item.name()],
        ]

        # default the return fields to None to use the python-api default
        fields = kwargs.get("fields", None)
        shots = sg.find("Shot", filter, fields=fields)
        if len(shots) > 1:
            # can not handle multiple shots with the same name
            raise Exception("Multiple shots named '%s' found", item.name())
        if len(shots) == 0:
            # create shot in flow production tracking
            shot_data = {
                "code": item.name(),
                parent_field: parent,
                "project": self.parent.context.project,
            }
            shot = sg.create("Shot", shot_data, return_fields=fields)
            self.parent.log_info(
                "Created Shot in Flow Production Tracking: %s" % shot_data
            )
        else:
            shot = shots[0]
            self.parent.log_info("Found Shot in Flow Production Tracking: %s" % shot)

        # update the thumbnail for the shot
        upload_thumbnail = kwargs.get("upload_thumbnail", True)
        if upload_thumbnail:
            self.parent.execute_hook(
                "hook_upload_thumbnail",
                entity=shot,
                source=item.source(),
                item=item,
                task=kwargs.get("task"),
            )

        return shot

    def get_episode(self, data=None, hiero_sequence=None):
        """
        Return the flow production tracking episode for the given Nuke Studio items.
        We define this as any tag linked to the sequence that starts
        with 'Ep'.
        """

        # If we had setup Nuke Studio to work in an episode context, then we could
        # grab the episode directly from the current context. However in this example we are not doing this but here
        # would be the code.
        # return self.parent.context.entity

        self.parent.logger.debug(
            "Searching matching Episode in Flow Production Tracking for sequence: %s",
            hiero_sequence.name(),
        )

        # stick a lookup cache on the data object.
        if "epi_cache" not in data:
            data["epi_cache"] = {}

        self.parent.logger.debug(
            "epi_cache: %s",
            data["epi_cache"],
        )

        # find episode name from the tags on the sequence
        nuke_studio_episode = None
        for t in hiero_sequence.tags():
            if t.name().startswith("Ep:"):
                nuke_studio_episode = t
                break
        if not nuke_studio_episode:
            raise Exception(
                "No episode has been assigned to the sequence: %s"
                % hiero_sequence.name()
            )

        self.parent.logger.debug(
            "Searching episode: %s (%s)",
            nuke_studio_episode.name(),
            nuke_studio_episode.guid(),
        )

        # For performance reasons, lets check if we've already added the episode to the cache and reuse it
        # Its not a necessary step, but it speeds things up if we don't have to check flow production tracking for the episode again
        # this session.
        if nuke_studio_episode.guid() in data["epi_cache"]:
            self.parent.logger.debug(
                "Found episode in cache: %s",
                data["epi_cache"][nuke_studio_episode.guid()],
            )
            return data["epi_cache"][nuke_studio_episode.guid()]

        # episode not found in cache, grab it from  Flow Production Tracking
        sg = self.parent.shotgun
        filters = [
            ["project", "is", self.parent.context.project],
            ["code", "is", nuke_studio_episode.name()[3:]],
        ]
        episodes = sg.find("Episode", filters, ["code"])
        if len(episodes) > 1:
            # can not handle multiple episodes with the same name
            raise Exception(
                "Multiple episodes named '%s' found" % nuke_studio_episode.name()[3:]
            )

        if len(episodes) == 0:
            # no episode has previously been created with this name
            # so we must create it in flow production tracking
            epi_data = {
                "code": nuke_studio_episode.name()[3:],
                "project": self.parent.context.project,
            }
            episode = sg.create("Episode", epi_data)
            self.parent.log_info(
                "Created Episode in Flow Production Tracking: %s" % epi_data
            )
        else:
            # we found one episode matching this name in flow production tracking, so we will resuse it, instead of creating a new one
            episode = episodes[0]
            self.parent.log_info(
                "Found matching Episode in Flow Production Tracking: %s" % episode
            )

        # update the cache with the results
        data["epi_cache"][nuke_studio_episode.guid()] = episode

        return episode

    def get_shot_parent(self, hiero_sequence, data, **kwargs):
        """
        Given a Hiero sequence and data cache, return the corresponding entity
        in Flow Production Tracking to serve as the parent for contained Shots.

        :param hiero_sequence: A Hiero sequence object
        :param dict data: A dictionary with cached parent data.

        :returns: A Shotgun entity.
        :rtype: dict

        .. note:: The data dict is typically the app's `preprocess_data` which maintains the cache across invocations of this hook.
        """

        self.parent.logger.debug(
            "Searching shot parent in Flow Production Tracking: %s",
            hiero_sequence.name(),
        )

        # stick a lookup cache on the data object.
        if "parent_cache" not in data:
            data["parent_cache"] = {}

        if hiero_sequence.guid() in data["parent_cache"]:
            return data["parent_cache"][hiero_sequence.guid()]

        episode = self.get_episode(data, hiero_sequence)

        # parent not found in cache, grab it from Flow Production Tracking

        sg = self.parent.shotgun
        filter = [
            ["project", "is", self.parent.context.project],
            ["code", "is", hiero_sequence.name()],
            ["episode", "is", episode],
        ]

        # the entity type of the parent.
        par_entity_type = "Sequence"

        parents = sg.find(par_entity_type, filter)
        if len(parents) > 1:
            # can not handle multiple parents with the same name
            raise Exception(
                "Multiple %s entities named '%s' found"
                % (par_entity_type, hiero_sequence.name())
            )

        if len(parents) == 0:
            # create the parent in flow production tracking
            par_data = {
                "code": hiero_sequence.name(),
                "project": self.parent.context.project,
                "episode": episode,
            }
            parent = sg.create(par_entity_type, par_data)
            self.parent.log_info(
                "Created %s in Flow Production Tracking: %s"
                % (par_entity_type, par_data.update(parent))
            )
        else:
            parent = parents[0]

        # update the thumbnail for the parent
        upload_thumbnail = kwargs.get("upload_thumbnail", True)
        if upload_thumbnail:
            self.parent.execute_hook(
                "hook_upload_thumbnail", entity=parent, source=hiero_sequence, item=None
            )

        # cache the results
        data["parent_cache"][hiero_sequence.guid()] = parent

        return parent
