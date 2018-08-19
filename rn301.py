import logging
import xml.etree.ElementTree as ET

from typing import Optional

import voluptuous as vol
import requests

from homeassistant.components.media_player import (
    MEDIA_PLAYER_SCHEMA, MEDIA_TYPE_MUSIC, MEDIA_TYPE_CHANNEL, MEDIA_TYPE_PLAYLIST, PLATFORM_SCHEMA,
    SUPPORT_NEXT_TRACK, SUPPORT_PAUSE, SUPPORT_PLAY, SUPPORT_PLAY_MEDIA,
    SUPPORT_PREVIOUS_TRACK, SUPPORT_SELECT_SOURCE, SUPPORT_STOP,
    SUPPORT_TURN_OFF, SUPPORT_TURN_ON, SUPPORT_VOLUME_MUTE, SUPPORT_VOLUME_SET,
    SUPPORT_SHUFFLE_SET, SUPPORT_SEEK,
    MediaPlayerDevice)
from homeassistant.const import (
    CONF_HOST, CONF_NAME, STATE_ON,
    STATE_OFF, STATE_IDLE, STATE_PLAYING, STATE_UNKNOWN, ATTR_ENTITY_ID,
    SERVICE_TOGGLE, SERVICE_TURN_ON, SERVICE_TURN_OFF, SERVICE_VOLUME_UP,
    SERVICE_MEDIA_PLAY, SERVICE_MEDIA_SEEK, SERVICE_MEDIA_STOP,
    SERVICE_VOLUME_SET, SERVICE_MEDIA_PAUSE, SERVICE_SHUFFLE_SET,
    SERVICE_VOLUME_DOWN, SERVICE_VOLUME_MUTE, SERVICE_MEDIA_NEXT_TRACK,
    SERVICE_MEDIA_PLAY_PAUSE, SERVICE_MEDIA_PREVIOUS_TRACK)

import homeassistant.util.dt as dt_util
import homeassistant.helpers.config_validation as cv

DOMAIN = 'rn301'

ATTR_ENABLED = 'enabled'
ATTR_PORT = 'port'
DATA_YAMAHA = 'yamaha_known_receivers'
DEFAULT_NAME = 'Yamaha R-N301'
DEFAULT_TIMEOUT = 5
BASE_URL = 'http://{0}/YamahaRemoteControl/ctrl'

ENABLE_OUTPUT_SCHEMA = MEDIA_PLAYER_SCHEMA.extend({
    vol.Required(ATTR_ENABLED): cv.boolean,
    vol.Required(ATTR_PORT): cv.string,
})
SERVICE_ENABLE_OUTPUT = 'yamaha_enable_output'
SUPPORT_YAMAHA = SUPPORT_VOLUME_SET | SUPPORT_VOLUME_MUTE | \
    SUPPORT_TURN_ON | SUPPORT_TURN_OFF | SUPPORT_SELECT_SOURCE | \
    SUPPORT_PLAY | SUPPORT_PLAY_MEDIA | SUPPORT_PAUSE | SUPPORT_STOP | \
    SUPPORT_NEXT_TRACK | SUPPORT_PREVIOUS_TRACK | SUPPORT_SHUFFLE_SET | \
    SUPPORT_NEXT_TRACK | SUPPORT_PREVIOUS_TRACK | SUPPORT_SEEK

SUPPORTED_PLAYBACK = SUPPORT_VOLUME_SET | SUPPORT_VOLUME_MUTE | \
    SUPPORT_TURN_ON | SUPPORT_TURN_OFF | SUPPORT_SELECT_SOURCE

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Required(CONF_HOST): cv.string})
SOURCE_MAPPING = {
    'TV': 'OPTICAL',
    'Chromecast Audio': 'CD',
    'Spotify': 'Spotify',
    'Decks': 'LINE1',
    'Web Radio': 'NET RADIO',
    'DLNA': 'SERVER'
}

_LOGGER = logging.getLogger(__name__)


def setup_platform(hass, config, add_devices, discovery_info=None):
    devices = []
    device = YamahaRn301MP(config.get(CONF_NAME), config.get(CONF_HOST))
    devices.append(device)
    add_devices(devices)

class YamahaRn301MP(MediaPlayerDevice):

    def __init__(self, name, host):
        self._data = None
        self._name = name
        self._host = host
        self._base_url = BASE_URL.format(self._host)
        self._pwstate = STATE_UNKNOWN
        self._volume = 0
        self._muted = False
        self._is_on = None
        self._current_state = -1
        self._current_operation = ''
        self._set_state = None
        self._source = None
        self._device_source = None
        self._source_list = list(SOURCE_MAPPING.keys())
        self._reverse_mapping = {val: key for key, val in SOURCE_MAPPING.items()}
        self._operation_list = ['On', 'Vol']

        self._media_meta = {}
        self._media_playing = False
        self._media_play_position = None
        self._media_play_position_updated = None
        self._media_play_shuffle = None
        self._media_play_repeat = None
        self._media_play_artist = None
        self._media_play_album = None
        self._media_play_song = None
        self._media_playback_state = None
        _LOGGER.debug("Init called")
        self.update()

    def _do_api_request(self, data) -> str:
        data = '<?xml version="1.0" encoding="utf-8"?>' + data
        req = requests.post(self._base_url, data=data, timeout=DEFAULT_TIMEOUT)
        if req.status_code != 200:
            _LOGGER.exception("Error doing API request, %d, %s", req.status_code, data)
        else:
            _LOGGER.debug("API request ok %d", req.status_code)
        return req.text

    def _do_api_get(self, data) -> str:
        data = '<YAMAHA_AV cmd="GET">' + data + '</YAMAHA_AV>'
        return self._do_api_request(data)

    def _do_api_put(self, data) -> str:
        data = '<YAMAHA_AV cmd="PUT">' + data + '</YAMAHA_AV>'
        return self._do_api_request(data)

    def update(self) -> None:
        data = self._do_api_get("<Main_Zone><Basic_Status>GetParam</Basic_Status></Main_Zone>")
        tree = ET.fromstring(data)
        for node in tree[0][0]:
            if node.tag == "Power_Control":
                self._pwstate = STATE_IDLE if (node[0].text) == "On" else STATE_OFF
            elif node.tag == "Volume":
                for voln in node:
                    if voln.tag == "Lvl":
                        self._volume = int(voln.find("Val").text) / 50
                    elif voln.tag == "Mute":
                        self._muted = voln.text == "On"
            elif node.tag == "Input":

                txt = node.find("Input_Sel").text
                self._source = self._reverse_mapping[txt]
                self._device_source = txt.replace(" ", "_")
        self._update_media_playing()


    @property
    def state(self):
        return self._pwstate

    @property
    def supported_features(self):
        if self._source in ("TV", "Chromecast Audio", "Decks"):
            return SUPPORTED_PLAYBACK
        return SUPPORT_YAMAHA

    @property
    def volume_level(self):
        return self._volume

    @property
    def source(self):
        return self._source

    @property
    def source_list(self):
        return self._source_list

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_volume_muted(self) -> bool:
        return self._muted

    def _nullify_media_fields(self) -> None:
        """Set media fields to null as we don't require them on certain channels"""
        self._media_meta = {}
        self._media_playing = False
        self._pwstate = STATE_IDLE if self._pwstate != STATE_OFF else STATE_OFF

    def _set_playback_info(self, text: str) -> None:
        """Set the playback info from xml"""
        if text == "Play":
            self._pwstate = STATE_PLAYING if self._pwstate != STATE_OFF else STATE_OFF
            self._media_playing = True
        elif text == "Pause":
            self._pwstate = STATE_IDLE if self._pwstate != STATE_OFF else STATE_OFF
            self._media_playing = True
        else:
            self._media_playing = False

    def _update_media_playing(self):
        media_meta_mapping = {
            'Artist': 'artist',
            'Station': 'station',
            'Album': 'album',
            'Song': 'song',
            'Track': 'song',
        }

        try:
            if self._device_source in ("Spotify", "NET_RADIO", "SERVER"):
                data = self._do_api_get("<{0}><Play_Info>GetParam</Play_Info></{0}>".format(self._device_source))
                self._media_meta = {}
                tree = ET.fromstring(data)
                for node in tree[0][0]:
                    if node.tag == "Play_Mode":
                        self._media_play_repeat = node.text == "On"
                        self._media_play_shuffle = node.text == "On"
                    elif node.tag == "Play_Time":
                        self._media_play_position = int(node.text)
                        self._media_play_position_updated = dt_util.utcnow()
                    elif node.tag == "Meta_Info":
                        for meta in node:
                            if meta.tag in media_meta_mapping and meta.text:
                                self._media_meta[media_meta_mapping[meta.tag]] = meta.text.replace('&amp;', '&')
                    elif node.tag == "Playback_Info":
                        self._set_playback_info(node.text)
            else:
                self._nullify_media_fields()
        except:
            _LOGGER.exception(data)

    @property
    def media_position(self):
        """Duration of current playing media"""
        return self._media_play_position

    @property
    def media_position_updated_at(self):
        """Duration of current playing media"""
        return self._media_play_position_updated

    @property
    def media_title(self):
        """Title of currently playing track"""
        return self._media_meta.get('song')

    @property
    def media_album(self):
        """Album of currently playing track"""
        return self._media_meta.get('album')

    @property
    def media_artist(self) -> Optional[str]:
        """Artist of currently playing track"""
        if self._source == "Net Radio":
            return self._media_meta.get('station')
        return self._media_meta.get('artist')

    @property
    def media_content_type(self):
        return MEDIA_TYPE_PLAYLIST

    @property
    def shuffle(self):
        return self._media_play_shuffle

    def set_shuffle(self):
        self._media_play_control("Shuffle")

    def _set_power_state(self, on):
        self._do_api_put('<System><Power_Control><Power>{0}</Power></Power_Control></System>'.format("On" if on else "Standby"))

    def turn_on(self):
        """Turn on the amplifier"""
        self._set_power_state(True)

    def turn_off(self):
        """Turn off the amplifier"""
        self._set_power_state(False)

    def set_volume_level(self, volume):
        self._do_api_put('<Main_Zone><Volume><Lvl><Val>{0}</Val><Exp>0</Exp><Unit></Unit></Lvl></Volume></Main_Zone>'.format(int(volume * 50)))

    def select_source(self, source):
        self._do_api_put('<Main_Zone><Input><Input_Sel>{0}</Input_Sel></Input></Main_Zone>'.format(SOURCE_MAPPING[source]))

    def mute_volume(self, mute):
        self._do_api_put('<System><Volume><Mute>{0}</Mute></Volume></System>'.format('On' if mute else 'Off'))
        self._muted = mute

    def _media_play_control(self, command):
        self._do_api_put('<{0}><Play_Control><Playback>{1}</Playback></Play_Control></{0}>'.format(self._device_source, command))

    def media_play(self):
        """Play media"""
        self._media_play_control("Play")

    def media_pause(self):
        """Play media"""
        self._media_play_control("Pause")

    def media_stop(self):
        """Play media"""
        self._media_play_control("Stop")

    def media_next_track(self):
        self._media_play_control("Skip Fwd")

    def media_previous_track(self):
        self._media_play_control("Skip Rev")
