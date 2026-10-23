"""Third-party service endpoints used by the backend."""

ADSBFI_MILITARY = "https://opendata.adsb.fi/api/v2/mil"
AISSTREAM_WEBSOCKET = "wss://stream.aisstream.io/v0/stream"
DIGITRAFFIC_MQTT_HOST = "meri.digitraffic.fi"
DIGITRAFFIC_MQTT_PORT = 443
DIGITRAFFIC_MQTT_PATH = "/mqtt"

CELESTRAK_TLE = "https://celestrak.org/NORAD/elements/gp.php?GROUP={group}&FORMAT=tle"
SATNOGS_TLE = "https://db.satnogs.org/api/tle/?format=json"

PHOTON_REVERSE = "https://photon.komoot.io/reverse"
WIKIDATA_ITEMS = "https://www.wikidata.org/w/rest.php/wikibase/v1/entities/items"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary"
WIKIMEDIA_FILE = "https://commons.wikimedia.org/wiki/Special:FilePath"
BIGDATACLOUD_REVERSE = "https://api.bigdatacloud.net/data/reverse-geocode-client"
USGS_EARTHQUAKES = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
CARTO_DARK_STYLE = "https://basemaps.cartocdn.com/gl/dark-matter-gl-style/style.json"
CARTO_TILE_HOST = "cartocdn.com"
ARCGIS_IMAGERY_TILE = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
TERRARIUM_TILE = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
