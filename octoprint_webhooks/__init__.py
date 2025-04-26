# -*- coding: utf-8 -*-
from __future__ import absolute_import, unicode_literals

import json
import requests
import time
import sys

from datetime import datetime

from io import BytesIO
from PIL import Image

import octoprint.plugin
from octoprint.events import eventManager, Events

# Returns if the variable is a unicode or string accounting for both python 2 & 3.
def is_string(unicode_or_str):
	is_string = False
	if sys.version_info[0] >= 3:
		if type(unicode_or_str) is str:
			is_string = True
	else:
		if type(unicode_or_str) is unicode or type(unicode_or_str) is str:
			is_string = True
			
	return is_string

# Replaces any v in data that start with @param
# with the v in values. For instance, if data
# was {"abc":"@p1"}, and values was {"p1":"123"},
# then @p1 would get replaced with 123 like so:
# {"abc":"123"}
def replace_dict_with_data(d, v):
	looping_over = d
	if type(d) is list:
		looping_over = range(0, len(d))
		
	for key in looping_over:
		value = d[key]
		if type(value) is dict:
			d[key] = replace_dict_with_data(value, v)
		elif is_string(value):
			# Loop until all @params are replaced
			while is_string(d[key]) and d[key].find("@") >= 0:
				start_index = d[key].find("@")
				# Find the end text by space
				end_index = d[key].find(" ", start_index)

				if end_index == -1:
					end_index = len(d[key])

				value_key = d[key][start_index + 1:end_index]

				# Check for dot notation
				components = value_key.split(".")
				current_v = v
				comp_found = True

				for ic in range(0, len(components)):
					comp = components[ic]
					if comp in current_v:
						current_v = current_v[comp]
					else:
						comp_found = False
						break

				if not comp_found:
					current_v = ""

				if start_index == 0 and end_index == len(d[key]):
					d[key] = current_v

				else:
					d[key] = d[key].replace(d[key][start_index:end_index], str(current_v))

		elif type(value) is list:
			d[key] = replace_dict_with_data(value, v)

	return d

# Replaces any @param in the url with data inside the dictionary.
# Works very similar to the function replace_dict_with_data.
def replace_url_with_data(url, data):
	value = url
	while value.find("@") >= 0:
		start_index = value.find("@")
		# Find the end text by space
		end_index1 = value.find("/", start_index)

		if end_index1 == -1:
			end_index1 = len(value)

		end_index2 = value.find(" ", start_index)
		if end_index2 == -1:
			end_index2 = len(value)

		end_index3 = value.find("?", start_index)
		if end_index3 == -1:
			end_index3 = len(value)

		end_index4 = value.find("#", start_index)
		if end_index4 == -1:
			end_index4 = len(value)

		end_index = min(end_index1, end_index2, end_index3, end_index4)
		value_key = value[start_index + 1:end_index]

		# Check for dot notation
		components = value_key.split(".")
		current_v = data
		comp_found = True

		for ic in range(0, len(components)):
			comp = components[ic]
			if comp in current_v:
				current_v = current_v[comp]
			else:
				comp_found = False
				break

		if not comp_found:
			current_v = ""

		if start_index == 0 and end_index == len(value):
			value = current_v
		else:
			value = value.replace(value[start_index:end_index], str(current_v))

	return value

# Checks for the name/value pair to make sure it matches
# and if not sets the name/value and returns
def check_for_header(headers, name, value):
	is_set = False
	for key in headers:
		if name.lower() in key.lower():
			is_set = True
			if value.lower() not in headers[key].lower():
				headers[key] = value

	if not is_set:
		headers[name] = value

	return headers

# Any inner dictionaries will be json encoded so that they can be passed correctly
# to work with python requests.
def inner_json_encode(data):
	try:
		for key in data:
			if type(data[key]) is dict or type(data[key]) is list:
				data[key] = json.dumps(data[key])
	except Exception as e:
		print(str(e))
	return data

class WebhooksPlugin(octoprint.plugin.StartupPlugin, octoprint.plugin.TemplatePlugin, octoprint.plugin.SettingsPlugin,
					 octoprint.plugin.EventHandlerPlugin, octoprint.plugin.AssetPlugin, octoprint.plugin.SimpleApiPlugin,
					 octoprint.plugin.ProgressPlugin):
	def __init__(self):
		self.triggered = False
		self.last_print_progress = -1
		self.last_print_progress_milestones = []

	def get_update_information(self, *args, **kwargs):
		return dict(
			webhooks=dict(
				displayName=self._plugin_name,
				displayVersion=self._plugin_version,
				type="github_release",
				current=self._plugin_version,
				user="derekantrican",
				repo="OctoPrint-Webhooks",
				pip="https://github.com/derekantrican/OctoPrint-Webhooks/archive/{target}.zip"
			)
		)

	def on_after_startup(self):
		self._logger.info("WebHooks Plugin Starting")
		# Update the settings if necessary
		self.migrate_settings()

	def migrate_settings(self):
		# Repeatedly checking 'self._settings.get(["settings_version"])' allows us to "recursively" migrate
		# the settings (eg a user is on settings_version 2, and we can upgrade them all the way to the latest
		# with a single call of this method - avoiding multiple restarts of Octoprint)

		if self._settings.get(["settings_version"]) == 1:
			self._logger.info("Migrating settings from v1 to v2")

			# create a hook with the current params and add it to the list
			hook_params = ["url", "apiSecret", "deviceIdentifier", "eventPrintStarted", "eventPrintDone",
						   "eventPrintFailed", "eventPrintPaused", "eventUserActionNeeded", "eventError",
						   "event_print_progress", "event_print_progress_interval", "eventPrintStartedMessage",
						   "eventPrintDoneMessage", "eventPrintFailedMessage", "eventPrintPausedMessage",
						   "eventUserActionNeededMessage", "eventPrintProgressMessage", "eventErrorMessage",
						   "headers", "data", "http_method", "content_type", "oauth", "oauth_url", "oauth_headers",
						   "oauth_data", "oauth_http_method", "oauth_content_type", "test_event", "webhook_enabled",
						   "event_cooldown", "verify_ssl"]
			
			hooks = self._settings.get(["hooks"])
			hook = dict()

			for i in range(0, len(hook_params)):
				key = hook_params[i]
				hook[key] = self._settings.get([key])

			# now store the hook
			self._logger.info("New Hook: " + str(hook))
			hooks = [hook]
			self._settings.set(["hooks"], hooks)
			self._settings.set(["settings_version"], 2)
			self._settings.save()
			self._logger.info("Hooks: " + str(self._settings.get(["hooks"])))

		if self._settings.get(["settings_version"]) == 2:
			self._logger.info("Migrating settings from v2 to v3")

			hooks = self._settings.get(["hooks"])
			for hook_index in range(0, len(hooks)):
				hook = hooks[hook_index]
				hook["customEvents"] = []

			self._settings.set(["hooks"], hooks)
			self._settings.set(["settings_version"], 3)
			self._settings.save()

		if self._settings.get(["settings_version"]) == 3:
			self._logger.info("Migrating settings from v3 to v4")
			
			hooks = self._settings.get(["hooks"])
			for hook_index in range(0, len(hooks)):
				hook = hooks[hook_index]
				hook["event_cooldown"] = 0

			self._settings.set(["hooks"], hooks)
			self._settings.set(["settings_version"], 4)
			self._settings.save()
		
		if self._settings.get(["settings_version"]) == 4:
			self._logger.info("Migrating settings from v4 to v5")

			hooks = self._settings.get(["hooks"])
			for hook_index in range(0, len(hooks)):
				hook = hooks[hook_index]
				hook["verify_ssl"] = True

			self._settings.set(["hooks"], hooks)
			self._settings.set(["settings_version"], 5)
			self._settings.save()

	def get_settings_defaults(self):
		return dict(
			hooks = [dict(
				url = "",
				apiSecret = "",
				deviceIdentifier = "",
				eventPrintStarted = True,
				eventPrintDone = True,
				eventPrintFailed = True,
				eventPrintPaused = True,
				eventUserActionNeeded = True,
				eventError = True,
				event_print_progress = False,
				event_print_progress_interval = "50",
				eventPrintStartedMessage = "Your print has started.",
				eventPrintDoneMessage = "Your print is done.",
				eventPrintFailedMessage = "Something went wrong and your print has failed.",
				eventPrintPausedMessage = "Your print has paused. You might need to change the filament color.",
				eventUserActionNeededMessage = "User action needed. You might need to change the filament color.",
				eventPrintProgressMessage = "Your print is @percentCompleteMilestone % complete.",
				eventErrorMessage = "There was an error.",
				customEvents = [],
				verify_ssl = True,
				headers = '{\n  "Content-Type":"application/json"\n}',
				data = '{\n  "deviceIdentifier":"@deviceIdentifier",\n  "apiSecret":"@apiSecret",\n  "topic":"@topic",\n  "message":"@message",\n  "extra":"@extra",\n  "state": "@state",\n  "job": "@job",\n  "progress": "@progress",\n  "currentZ": "@currentZ",\n  "offsets": "@offsets",\n  "meta": "@meta",\n  "currentTime": "@currentTime",\n  "snapshot": "@snapshot"\n}',
				http_method = "POST",
				content_type = "JSON",
				oauth = False,
				oauth_url = "",
				oauth_headers = '{\n  "Content-Type": "application/json"\n}',
				oauth_data = '{\n  "client_id":"myClient",\n  "client_secret":"mySecret",\n  "grant_type":"client_credentials"\n}',
				oauth_http_method = "POST",
				oauth_content_type = "JSON",
				test_event = "PrintStarted",
				webhook_enabled = True,
				event_cooldown = 0,
			)],
			settings_version = 4
		)

	def get_template_configs(self):
		return [
			dict(type="settings", custom_bindings=True)
		]

	def get_assets(self):
		return dict(
			css=["css/webhooks.css"],
			js=["js/webhooks.js"],
			json=[
				"templates/simple.json",
				"templates/fulldata.json",
				"templates/snapshot.json",
				"templates/oauth.json",
				"templates/dotnotation.json",
				"templates/slack.json",
				"templates/plivo.json",
				"templates/alexa_notify_me.json"
			]
		)

	def register_custom_events(self, *args, **kwargs):
		return ["notify", "progress"]

	def on_print_progress(self, storage, path, progress):
		# Reset in case of multiple prints
		if self.last_print_progress > progress:
			self.last_print_progress = -1
			self.last_print_progress_milestones = []
		
		# Get the settings
		hooks = self._settings.get(["hooks"])
		for hook_index in range(0, len(hooks)):
			hook = hooks[hook_index]
			active = hook["event_print_progress"]
			event_print_progress_interval = hook["event_print_progress_interval"]
			#self._logger.info("Print Progress" + storage + " - " + path + " - {0}".format(progress) + " - hook_index:{0}".format(hook_index) + " - active:{0}".format(active))

			if active:
				try:
					interval = int(event_print_progress_interval)

					# Now loop over all the missed progress events and see if they match
					for p in range(self.last_print_progress + 1, progress + 1):
						if p % interval == 0 and p != 0 and p != 100:

							# Send the event for print progress
							if len(self.last_print_progress_milestones) > hook_index:
								self.last_print_progress_milestones[hook_index] = p
							else:
								self.last_print_progress_milestones.append(p)
							
							#self._logger.info("Fire Print Progress Event {0}".format(p))
							eventManager().fire(Events.PLUGIN_WEBHOOKS_PROGRESS)

					# Update the last print progress
					self.last_print_progress = progress

				except Exception as e:
					self._plugin_manager.send_plugin_message(self._identifier, dict(type="error", hide=True, msg="Invalid Setting for PRINT PROGRESS INTERVAL please use a number without any special characters instead of " + event_print_progress_interval))
					continue

	def get_api_commands(self):
		return dict(
			testhook=[],
			savehooks=[]
		)

	def on_api_command(self, command, data):
		if command == "testhook":
			# self._logger.info("API testhook CALLED!")
			# TRIGGER A CUSTOM EVENT FOR A TEST PAYLOAD
			event_name = ""
			if "event" in data:
				event_name = data["event"]

			if event_name == "plugin_webhooks_progress":
				hooks = self._settings.get(["hooks"])
				for hook_index in range(0, len(hooks)):
					if len(self.last_print_progress_milestones) > hook_index:
						self.last_print_progress_milestones[hook_index] = 50
					else:
						self.last_print_progress_milestones.append(50)

			event_data = {
				"name": "example.gcode",
				"path": "example.gcode",
				"origin": "local",
				"size": 242038,
				"owner": "example_user",
				"time": 50.237335886,
				"popup": True
			}

			if "hook_index" in data:
				event_data["hook_index"] = int(data["hook_index"])

			self.on_event(event_name, event_data)
		elif command == "savehooks":
			self._logger.info("savesettings")
			if "settings" in data and "hooks" in data["settings"]:
				self._settings.set(["hooks"], data["settings"]["hooks"])
				self._logger.info("Updated hooks")
			else:
				self._logger.info("Unable to update hooks - invalid data")

	# Returns a dictionary of the current job information
	def get_job_information(self):
		# Call the api
		try:
			rd = self._printer.get_current_data()
			# Get the path if it exists
			if "job" in rd and "file" in rd["job"] and "path" in rd["job"]["file"]:
				path = rd["job"]["file"]["path"]
				if type(path) is str:
					if self._file_manager.file_exists(rd["job"]["file"]["origin"], path):
						# self._logger.info("file exists at path")
						# Get the file metadata, analysis, ...
						meta = self._file_manager.get_metadata(rd["job"]["file"]["origin"], path)
						metadata = {
							"meta": meta
						}

						rd.update(metadata)
					else:
						self._logger.debug(f"file does not exist at {path}")

			# self._logger.info("getting job info" + json.dumps(rd))
			return rd
		
		except Exception as e:
			self._logger.warn("get_job_information exception: " + str(e))
			return {}

	event_times = {}

	import threading
from urllib.parse import urlparse

def is_valid_url(url):
    parsed = urlparse(url)
    return parsed.scheme in ('http', 'https') and parsed.netloc

def send_webhook(url, event, payload, logger):
    try:
        response = requests.post(
            url,
            json=dict(event=event, payload=payload),
            timeout=5
        )
        if response.status_code >= 400:
            logger.warning(f"Webhook responded with error code {response.status_code}")
    except requests.RequestException as e:
        logger.exception(f"Error sending webhook: {str(e)}")

def on_event(self, event, payload):
    if self._settings.get(["enabled"]):
        if event in self._settings.get(["events"]):
            url = self._settings.get(["url"])
            if url and is_valid_url(url):
                threading.Thread(target=send_webhook, args=(url, event, payload, self._logger)).start()
            else:
                self._logger.error("Invalid webhook URL configured or missing URL.")


	def recv_callback(self, comm_instance, line, *args, **kwargs):
		# Found keyword, fire event and block until other text is received
		if "echo:busy: paused for user" in line:
			if not self.triggered:
				eventManager().fire(Events.PLUGIN_WEBHOOKS_NOTIFY)
				self.triggered = True
		# Other text, we may fire another event if we encounter "paused for user" again
		else:
			self.triggered = False
		return line

		# Private functions - Print Job Notifications

	# Create an image by getting an image from the setting webcam-snapshot.
	# Transpose this image according the settings and returns it
	# :return:
	def get_snapshot(self):
		# 1) Get the snapshot url if set and other webcam settings
		self._logger.debug("Getting Snapshot")
		snapshot_url = self._settings.global_get(["webcam", "snapshot"])
		hflip = self._settings.global_get(["webcam", "flipH"])
		vflip = self._settings.global_get(["webcam", "flipV"])
		rotate = self._settings.global_get(["webcam", "rotate90"])
		self._logger.debug("Snapshot URL: " + str(snapshot_url))
		
		if type(snapshot_url) is not str:
			return None

		# 2) Get the image data from the snapshot url
		image = None
		try:
			# Reduce the resolution of image to prevent 400 error when uploading content
			# Besides this saves network bandwidth and Android device or WearOS
			# cannot tell the difference in resolution
			image = requests.get(snapshot_url, stream=True).content
			image_obj = Image.open(BytesIO(image))

			# 3) Now resize the image so that it isn't too big to send.
			x, y = image_obj.size
			if x > 1640 or y > 1232:
				size = 1640, 1232
				image_obj.thumbnail(size, Image.ANTIALIAS)
				output = BytesIO()
				image_obj.save(output, format="JPEG")
				image = output.getvalue()
				output.close()
		except requests.exceptions.RequestException as e:
			self._logger.info("Error getting snapshot: " + str(e))
			return None
		except Exception as e:
			self._logger.info("Error reducing resolution of image: " + str(e))
			return None

		# 4) Flip or rotate the image if necessary
		if hflip or vflip or rotate:
			try:
				# https://www.blog.pythonlibrary.org/2017/10/05/how-to-rotate-mirror-photos-with-python/
				image_obj = Image.open(BytesIO(image))
				if hflip:
					image_obj = image_obj.transpose(Image.FLIP_LEFT_RIGHT)
				if vflip:
					image_obj = image_obj.transpose(Image.FLIP_TOP_BOTTOM)
				if rotate:
					image_obj = image_obj.rotate(90)

				# https://stackoverflow.com/questions/646286/python-pil-how-to-write-png-image-to-string/5504072
				output = BytesIO()
				image_obj.save(output, format="JPEG")
				image = output.getvalue()
				output.close()
			except Exception as e:
				self._logger.info("Error rotating image: " + str(e))
				return None
		return image


__plugin_name__ = "Webhooks"
__plugin_pythoncompat__ = ">=2.7,<4"


def __plugin_load__():
	global __plugin_implementation__
	__plugin_implementation__ = WebhooksPlugin()
	global __plugin_hooks__
	__plugin_hooks__ = {
		"octoprint.plugin.softwareupdate.check_config": __plugin_implementation__.get_update_information,
		"octoprint.comm.protocol.gcode.received": __plugin_implementation__.recv_callback,
		"octoprint.events.register_custom_events": __plugin_implementation__.register_custom_events
	}
