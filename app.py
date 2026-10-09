import json
import folium
import os
import io
import base64
from datetime import datetime, date, time

import pandas as pd
import requests
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image
from streamlit_geolocation import streamlit_geolocation

st.set_page_config(page_title="Taxi Kart", page_icon="🚕", layout="wide")
st.markdown(
    "<style>.block-container{padding-top:2.5rem;padding-bottom:0.5rem;}</style>",
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Character: avatar sitting in the kart
# ---------------------------------------------------------------------------
AVATAR_PATH = os.path.join("assets", "avatar.png")        # 512x64: 16 frames of 32x64
KART_SHEET_PATH = os.path.join("assets", "gokart.png")    # 512x32: 16 frames of 32x32

SCALE = 2
SPEED = 0.01              # degrees of latitude per second
MAP_HEIGHT = 780           # map height in pixels

AVATAR_IDLE = {"down": 0, "left": 3, "up": 6, "right": 9}
KART_FRAMES = {
    "right": [0, 1, 2, 3],
    "down": [4, 5, 6, 7],
    "left": [8, 9, 10, 11],
    "up": [12, 13, 14, 15],
}
DIRECTIONS = list(KART_FRAMES)

AVATAR_DY = -3
CROP_TOP = 16

START = (40.7831, -73.9712)

# Field labels (JS finds them using these: do not change without adapting LABELS)
LABEL_A = "Pickup (A)"
LABEL_B = "Dropoff (B)"


BANANA_PATH = os.path.join("assets", "banana.png")


@st.cache_resource
def banana_uri():
    """banana.png as a data URI (None if the file is missing -> emoji fallback)."""
    if not os.path.exists(BANANA_PATH):
        return None
    with open(BANANA_PATH, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def to_data_uri(img):
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@st.cache_data
def driver_icon(direction, kart_frame):
    avatar = Image.open(AVATAR_PATH).convert("RGBA")
    kart = Image.open(KART_SHEET_PATH).convert("RGBA")

    i = AVATAR_IDLE[direction]
    avatar_img = avatar.crop((i * 32, 0, i * 32 + 32, 64))
    j = KART_FRAMES[direction][kart_frame]
    kart_img = kart.crop((j * 32, 0, j * 32 + 32, 32))

    canvas = Image.new("RGBA", (32, 64), (0, 0, 0, 0))
    canvas.paste(avatar_img, (0, AVATAR_DY), avatar_img)
    canvas.alpha_composite(kart_img, (0, 32))
    canvas = canvas.crop((0, CROP_TOP, 32, 64))
    canvas = canvas.resize((canvas.width * SCALE, canvas.height * SCALE), Image.NEAREST)
    return to_data_uri(canvas), canvas.size


@st.cache_data(show_spinner=False)
def geocode(address):
    try:
        response = requests.get(
            "https://geosearch.planninglabs.nyc/v2/search",
            params={"text": address, "size": 1},
            timeout=10,
        )
        response.raise_for_status()
        features = response.json().get("features", [])
        if not features:
            return None
        lon, lat = features[0]["geometry"]["coordinates"]
        label = features[0]["properties"].get("label", address)
        return lat, lon, label
    except (requests.RequestException, KeyError, ValueError):
        return None


@st.cache_data(show_spinner=False)
def reverse_geocode(lat, lon):
    try:
        response = requests.get(
            "https://geosearch.planninglabs.nyc/v2/reverse",
            params={"point.lat": lat, "point.lon": lon, "size": 1},
            timeout=5,
        )
        response.raise_for_status()
        features = response.json().get("features", [])
        if features:
            return features[0]["properties"].get("label")
    except (requests.RequestException, KeyError, ValueError):
        pass
    return None


@st.cache_data(show_spinner="Calculating fare...", ttl=600)
def predict_fare(pickup_datetime, plat, plon, dlat, dlon, passengers):
    response = requests.get(
        "https://taxifare.lewagon.ai/predict",
        params={
            "pickup_datetime": pickup_datetime,
            "pickup_longitude": plon,
            "pickup_latitude": plat,
            "dropoff_longitude": dlon,
            "dropoff_latitude": dlat,
            "passenger_count": passengers,
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()["fare"]


def resolve(text):
    """Text -> (lat, lon, label). Accepts 'lat, lon' (placed with Space) or an address."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        lat_s, lon_s = text.split(",")
        lat, lon = float(lat_s), float(lon_s)
        label = reverse_geocode(round(lat, 5), round(lon, 5)) or "Point on map"
        return lat, lon, label
    except ValueError:
        return geocode(text)


# ---------------------------------------------------------------------------
# Game-style fare display (neon glow, count-up, floating coins)
# ---------------------------------------------------------------------------
def game_fare(fare):
    html = f"""
    <link href="https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap" rel="stylesheet">
    <style>
      body {{ margin:0; background:transparent; }}
      .hud {{
        position:relative; overflow:hidden; text-align:center;
        padding:18px 10px 20px; border-radius:14px;
        background:linear-gradient(160deg,#12002b,#001a3a);
        border:3px solid #0ff;
        box-shadow:0 0 12px #0ff, 0 0 30px #0ff6, inset 0 0 18px #0ff4;
        animation:frame 2s ease-in-out infinite alternate, pop .6s cubic-bezier(.2,2,.4,1);
        font-family:'Press Start 2P', 'Courier New', monospace;
      }}
      .label {{ color:#ff0; font-size:11px; letter-spacing:2px;
        text-shadow:0 0 6px #ff0; animation:blink 1s steps(2) infinite; }}
      .amount {{
        margin-top:14px; font-size:34px; color:#fff;
        text-shadow:0 0 6px #fff, 0 0 12px #0ff, 0 0 24px #0ff, 0 0 42px #f0f;
        animation:spinIn 1.1s cubic-bezier(.2,.85,.3,1.3) both,
                  pulse 1.2s ease-in-out infinite,
                  float 2.4s ease-in-out infinite;
      }}
      /* scanlines */
      .hud::after {{
        content:""; position:absolute; inset:0; pointer-events:none;
        background:repeating-linear-gradient(0deg,#0000 0 2px,#0000002e 2px 4px);
      }}
      /* shine sweep */
      .hud::before {{
        content:""; position:absolute; top:0; left:-60%; width:40%; height:100%;
        background:linear-gradient(100deg,#0000,#fff4,#0000);
        transform:skewX(-20deg); animation:shine 3s linear infinite;
      }}
      .coin {{ position:absolute; bottom:-20px; font-size:18px; opacity:0;
        animation:rise 3s linear infinite; }}
      @keyframes frame {{ from {{ box-shadow:0 0 10px #0ff,0 0 24px #0ff6,inset 0 0 14px #0ff3; border-color:#0ff; }}
                          to   {{ box-shadow:0 0 18px #f0f,0 0 44px #f0f8,inset 0 0 26px #f0f4; border-color:#f0f; }} }}
      @keyframes pulse {{ 0%,100% {{ transform:scale(1); }} 50% {{ transform:scale(1.08); }} }}
      @keyframes spinIn {{
        0%   {{ rotate:-1080deg; scale:.15; opacity:0; }}
        60%  {{ opacity:1; }}
        80%  {{ rotate:18deg; scale:1.18; }}
        100% {{ rotate:0deg; scale:1; opacity:1; }}
      }}
      @keyframes float {{ 0%,100% {{ translate:0 0; }} 50% {{ translate:0 -4px; }} }}
      @keyframes blink {{ 50% {{ opacity:.25; }} }}
      @keyframes pop   {{ from {{ transform:scale(.3); opacity:0; }} to {{ transform:scale(1); opacity:1; }} }}
      @keyframes shine {{ to {{ left:140%; }} }}
      @keyframes rise  {{ 0% {{ transform:translateY(0) rotate(0); opacity:0; }}
                          15% {{ opacity:1; }}
                          100% {{ transform:translateY(-130px) rotate(360deg); opacity:0; }} }}
    </style>

    <div class="hud">
      <div class="label">★ ESTIMATED FARE ★</div>
      <div class="amount" id="amt">$0.00</div>
      <span class="coin" style="left:10%;animation-delay:0s">🪙</span>
      <span class="coin" style="left:30%;animation-delay:.8s">⭐</span>
      <span class="coin" style="left:55%;animation-delay:1.6s">🪙</span>
      <span class="coin" style="left:78%;animation-delay:.4s">⭐</span>
      <span class="coin" style="left:90%;animation-delay:2.1s">🪙</span>
    </div>

    <script>
      // Count-up like an arcade score counter
      var target = {fare:.2f}, t0 = performance.now(), dur = 1200;
      var el = document.getElementById('amt');
      function step(now) {{
        var p = Math.min((now - t0) / dur, 1);
        var e = 1 - Math.pow(1 - p, 3);                 // ease-out
        el.textContent = '$' + (target * e).toLocaleString('en-US',
          {{minimumFractionDigits:2, maximumFractionDigits:2}});
        if (p < 1) requestAnimationFrame(step);
      }}
      requestAnimationFrame(step);
    </script>

    <script>
      // Spin in at the center of the whole screen, then fly into place.
      (function () {{
        try {{
        var P = window.parent, frame = window.frameElement;
        if (!P || !frame) return;
        var hud = document.querySelector('.hud');
        if (!hud) return;

        var reduce = false;
        try {{ reduce = P.matchMedia('(prefers-reduced-motion: reduce)').matches; }} catch (e) {{}}
        if (reduce) return;

        var rect = frame.getBoundingClientRect();
        var tw = frame.offsetWidth || rect.width, th = frame.offsetHeight || rect.height;
        if (!tw || !th) return;

        hud.style.visibility = 'hidden';

        if (!P.document.getElementById('farefx-font')) {{
          var fl = P.document.createElement('link');
          fl.id = 'farefx-font'; fl.rel = 'stylesheet';
          fl.href = 'https://fonts.googleapis.com/css2?family=Press+Start+2P&display=swap';
          P.document.head.appendChild(fl);
        }}

        var ov = P.document.createElement('div');
        ov.style.cssText = 'position:fixed;left:0;top:0;width:100vw;height:100vh;' +
          'pointer-events:none;z-index:2147483647;';

        var card = P.document.createElement('div');
        card.style.cssText =
          'position:absolute;left:50%;top:50%;width:' + tw + 'px;box-sizing:border-box;' +
          'transform:translate(-50%,-50%) rotate(-1080deg) scale(.2);opacity:0;' +
          'padding:18px 10px 20px;border-radius:14px;text-align:center;overflow:hidden;' +
          'background:linear-gradient(160deg,#12002b,#001a3a);border:3px solid #0ff;' +
          'box-shadow:0 0 12px #0ff,0 0 30px #0ff6,inset 0 0 18px #0ff4;' +
          'font-family:\\'Press Start 2P\\',\\'Courier New\\',monospace;color:#fff;';
        card.innerHTML =
          '<div style="color:#ff0;font-size:11px;letter-spacing:2px;text-shadow:0 0 6px #ff0">' +
          '★ ESTIMATED FARE ★</div>' +
          '<div style="margin-top:14px;font-size:34px;color:#fff;' +
          'text-shadow:0 0 6px #fff,0 0 12px #0ff,0 0 24px #0ff,0 0 42px #f0f">' +
          '${fare:.2f}</div>';

        ov.appendChild(card);
        P.document.body.appendChild(ov);

        function finish() {{ ov.remove(); hud.style.visibility = 'visible'; }}

        var spin = card.animate([
          {{ transform:'translate(-50%,-50%) rotate(-1080deg) scale(.2)', opacity:0 }},
          {{ transform:'translate(-50%,-50%) rotate(0deg) scale(1)', opacity:1 }}
        ], {{ duration:1100, easing:'cubic-bezier(.2,.85,.3,1.3)', fill:'forwards' }});

        spin.onfinish = function () {{
          var r = frame.getBoundingClientRect();
          var dx = (r.left + r.width / 2) - (P.innerWidth / 2);
          var dy = (r.top + r.height / 2) - (P.innerHeight / 2);
          var s = r.width ? r.width / tw : 1;
          var fly = card.animate([
            {{ transform:'translate(-50%,-50%) rotate(0deg) scale(1)' }},
            {{ transform:'translate(calc(-50% + ' + dx + 'px),calc(-50% + ' + dy + 'px)) ' +
                        'rotate(0deg) scale(' + s + ')' }}
          ], {{ duration:650, easing:'cubic-bezier(.4,.1,.2,1)', fill:'forwards' }});
          fly.onfinish = finish;
          fly.oncancel = finish;
        }};
        spin.oncancel = finish;
        }} catch (e) {{}}
      }})();
    </script>
    """
    components.html(html, height=150)


# ---------------------------------------------------------------------------
# Music
# ---------------------------------------------------------------------------
MUSIC_PATH = os.path.join("assets", "music.mp3")


@st.cache_resource
def music_data_uri():
    with open(MUSIC_PATH, "rb") as f:
        return "data:audio/mpeg;base64," + base64.b64encode(f.read()).decode()


MUSIC_JS = """
<script>
(function () {
  var P = window.parent, doc = P.document;

  // A single audio player for the whole session (survives Streamlit reloads)
  if (!P.__bgm) {
    var a = new P.Audio("__SRC__");
    a.loop = true;
    a.volume = 0.4;
    P.__bgm = {audio: a, on: true, unlocked: false};
  }
  var M = P.__bgm;

  // Top right button
  var btn = doc.getElementById('bgm-toggle');
  if (!btn) {
    btn = doc.createElement('button');
    btn.id = 'bgm-toggle';
    btn.tabIndex = -1;  // must not catch the Space key
    btn.style.cssText = 'position:fixed;top:10px;right:190px;z-index:1000000;' +
      'padding:6px 12px;border-radius:20px;border:1px solid #ccc;background:#fff;' +
      'font:600 14px sans-serif;cursor:pointer;box-shadow:0 1px 4px rgba(0,0,0,.25)';
    doc.body.appendChild(btn);
  }
  function render() { btn.textContent = M.on ? '🔊 Music' : '🔇 Music'; }

  function tryPlay() {
    if (!M.on || M.unlocked) return;
    M.audio.play().then(function () { M.unlocked = true; }).catch(function () {});
  }
  // Called on first click / first key press if autoplay was blocked
  P.__bgmUnlock = tryPlay;

  btn.onclick = function () {
    M.on = !M.on;
    if (M.on) { M.unlocked = false; tryPlay(); } else { M.audio.pause(); }
    render();
    btn.blur();
  };

  if (!M.bound) {
    M.bound = true;
    ['pointerdown', 'keydown'].forEach(function (ev) {
      doc.addEventListener(ev, function () { tryPlay(); }, true);
    });
  }

  render();
  tryPlay();  // immediate attempt on arrival
})();
</script>
"""


def add_music():
    if os.path.exists(MUSIC_PATH):
        components.html(MUSIC_JS.replace("__SRC__", music_data_uri()), height=0)


# ---------------------------------------------------------------------------
# Map
# ---------------------------------------------------------------------------
m = folium.Map(location=list(START), zoom_start=16)

# Geolocation is read before the map (displayed in the right panel)
col_map, col_info = st.columns([3, 1], gap="medium")

with col_info:
    st.subheader("🚕 Your Ride")
    # location = streamlit_geolocation()

df = None  # set to a DataFrame below if you re-enable geolocation
# if location and location.get("latitude") is not None and location.get("longitude") is not None:
#     df = pd.DataFrame([location])
#     folium.Marker(
#         location=[location["latitude"], location["longitude"]],
#         tooltip="Your location",
#         popup="Your location",
#         icon=folium.Icon(color="green"),
#     ).add_to(m)

geojson_path = os.path.join("data", "manhattan_neighborhoods.geojson")
cities_path = os.path.join("data", "lewagon_cities.csv")

if os.path.exists(cities_path):
    for _, city in pd.read_csv(cities_path).iterrows():
        if pd.notna(city.get("lat")) and pd.notna(city.get("lon")):
            folium.Marker(
                location=[city["lat"], city["lon"]],
                popup=city.get("city", "Location"),
                icon=folium.Icon(color="red", icon="info-sign"),
            ).add_to(m)


def color_function(feat):
    return "red" if feat["properties"].get("borough") == "Manhattan" else "blue"


if os.path.exists(geojson_path):
    folium.GeoJson(
        geojson_path,
        name="Manhattan neighborhoods",
        style_function=lambda feat: {
            "weight": 1,
            "color": "black",
            "opacity": 0.5,
            "fillColor": color_function(feat),
            "fillOpacity": 0.25,
        },
        highlight_function=lambda feat: {"weight": 2, "fillOpacity": 0.5},
        tooltip=folium.GeoJsonTooltip(
            fields=["name"], aliases=["Neighborhood"], localize=True
        ),
    ).add_to(m)


PLAYER_JS = """
window.addEventListener('load', function () {
  var map = __MAP__;
  var D = __DATA__;
  var S;
  try {  // state (position, A/B points) survives Streamlit page reloads
    window.parent.__playerState = window.parent.__playerState ||
      {lat: D.lat, lon: D.lon, dir: 'down', pts: {pickup: null, dropoff: null}};
    S = window.parent.__playerState;
  } catch (e) { S = {lat: D.lat, lon: D.lon, dir: 'down', pts: {pickup: null, dropoff: null}}; }
  if (!S.pts) S.pts = {pickup: null, dropoff: null};
  if (!S.bananas) S.bananas = [];
  if (typeof S.zoom !== 'number') S.zoom = D.baseZoom;

  var keyMap = {w:'up', z:'up', a:'left', q:'left', s:'down', d:'right'};
  var vecs = {up:[1,0], down:[-1,0], left:[0,-1], right:[0,1]};
  var held = [];
  var animT = 0, frame = 0, currentKey = null;

  function refreshIcon() {
    var key = S.dir + frame;
    if (key === currentKey) return;
    currentKey = key;
    var i = D.sprite[S.dir][frame];
    marker.setIcon(L.icon({iconUrl: i.url, iconSize: [i.w, i.h],
                           iconAnchor: [i.w / 2, Math.round(i.h * 0.75)]}));
  }

  var marker = L.marker([S.lat, S.lon], {keyboard: false, zIndexOffset: 1000}).addTo(map);
  refreshIcon();
  map.setView([S.lat, S.lon], S.zoom, {animate: false});
  map.on('zoomend', function () { S.zoom = map.getZoom(); });

  // ----- A / B Points -----
  var pinLayers = [];
  function pin(letter, color) {
    return L.divIcon({className: '', iconSize: [28, 28], iconAnchor: [14, 14],
      html: '<div style="width:28px;height:28px;border-radius:50%;background:' + color +
            ';color:#fff;font:bold 15px sans-serif;display:flex;align-items:center;' +
            'justify-content:center;border:3px solid #fff;box-shadow:0 1px 5px rgba(0,0,0,.5)">' +
            letter + '</div>'});
  }
  function drawPoints() {
    pinLayers.forEach(function (l) { map.removeLayer(l); });
    pinLayers = [];
    var p = S.pts;
    if (p.pickup) pinLayers.push(L.marker(p.pickup, {icon: pin('A', '#1f77e4'), keyboard: false}).addTo(map));
    if (p.dropoff) pinLayers.push(L.marker(p.dropoff, {icon: pin('B', '#f28c00'), keyboard: false}).addTo(map));
    if (p.pickup && p.dropoff)
      pinLayers.push(L.polyline([p.pickup, p.dropoff], {color: '#444', weight: 3, dashArray: '8 8'}).addTo(map));
  }
  drawPoints();

  // ----- Bananas (press B to drop one under the kart) -----
  var st = document.createElement('style');
  st.textContent = '@keyframes bananaPop{0%{transform:scale(0) rotate(-180deg);opacity:0}' +
    '60%{transform:scale(1.4) rotate(15deg);opacity:1}100%{transform:scale(1) rotate(0)}}' +
    '.banana-pop{animation:bananaPop .35s ease-out;filter:drop-shadow(0 2px 2px rgba(0,0,0,.45))}';
  document.head.appendChild(st);

  var bananaLayers = [];
  function bananaIcon() {
    var inner = D.banana
      ? '<img class="banana-pop" src="' + D.banana + '" style="width:32px;height:32px;image-rendering:pixelated">'
      : '<div class="banana-pop" style="font-size:26px;line-height:32px;text-align:center">🍌</div>';
    return L.divIcon({className: '', iconSize: [32, 32], iconAnchor: [16, 16], html: inner});
  }
  function drawBananas() {
    bananaLayers.forEach(function (l) { map.removeLayer(l); });
    bananaLayers = S.bananas.map(function (b) {
      return L.marker(b, {icon: bananaIcon(), keyboard: false, zIndexOffset: 500}).addTo(map);
    });
  }
  function dropBanana() {
    S.bananas.push([+S.lat.toFixed(6), +S.lon.toFixed(6)]);
    var b = S.bananas[S.bananas.length - 1];
    bananaLayers.push(L.marker(b, {icon: bananaIcon(), keyboard: false, zIndexOffset: 500}).addTo(map));
  }
  drawBananas();

  // Writes a value in a Streamlit text field on the page and validates the input
  function setField(label, value) {
    try {
      var P = window.parent;
      var el = P.document.querySelector('input[aria-label="' + label + '"]');
      if (!el) return;
      var setter = Object.getOwnPropertyDescriptor(P.HTMLInputElement.prototype, 'value').set;
      setter.call(el, value);
      el.dispatchEvent(new P.Event('input', {bubbles: true}));
      // Validates input without moving focus (React listens to focusout for onBlur)
      el.dispatchEvent(new P.FocusEvent('focusout', {bubbles: true}));
    } catch (e) {}
  }

  // Gives focus back to the map (the iframe), including after Streamlit rerun
  function giveFocusBack() {
    try {
      var P = window.parent;
      var a = P.document.activeElement;
      if (a && a.tagName && a.tagName.toLowerCase() === 'input') a.blur();
    } catch (e) {}
    window.focus();
  }

  function placePoint() {
    var p = [+S.lat.toFixed(6), +S.lon.toFixed(6)];
    var txt = p[0].toFixed(6) + ', ' + p[1].toFixed(6);
    if (!S.pts.pickup || S.pts.dropoff) {          // new pickup
      S.pts = {pickup: p, dropoff: null};
      setField(D.labelB, '');
      setField(D.labelA, txt);
    } else {                                       // dropoff
      S.pts.dropoff = p;
      setField(D.labelB, txt);
    }
    drawPoints();
    giveFocusBack();
    setTimeout(giveFocusBack, 150);
    setTimeout(giveFocusBack, 600);   // after Streamlit rerun
  }

  // ----- Keyboard -----
  function onDown(e) {
    var t = (e.target.tagName || '').toLowerCase();
    if (t === 'input' || t === 'textarea' || t === 'select' || e.target.isContentEditable) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    var isSpace = (e.code === 'Space' || e.key === ' ');
    var d = keyMap[(e.key || '').toLowerCase()];
    var isBanana = ((e.key || '').toLowerCase() === 'b');
    if (!isSpace && !d && !isBanana) return;
    e.preventDefault();
    if (t === 'button' && e.target.blur) e.target.blur();
    if (isSpace) { if (!e.repeat) placePoint(); return; }
    if (isBanana) { if (!e.repeat) dropBanana(); return; }
    if (held.indexOf(d) === -1) held.push(d);
  }
  function onUp(e) {
    var d = keyMap[(e.key || '').toLowerCase()];
    if (d) held = held.filter(function (x) { return x !== d; });
  }
  window.addEventListener('keydown', onDown);
  window.addEventListener('keyup', onUp);
  window.addEventListener('blur', function () { held = []; });
  try {  // also when focus is on the rest of the Streamlit page
    var P = window.parent;
    P.__playerKeyDown = onDown; P.__playerKeyUp = onUp;
    if (!P.__playerFwd) {
      P.__playerFwd = true;
      P.document.addEventListener('keydown', function (e) { try { P.__playerKeyDown(e); } catch (x) {} });
      P.document.addEventListener('keyup', function (e) { try { P.__playerKeyUp(e); } catch (x) {} });
    }
  } catch (e) {}

  ['pointerdown', 'keydown'].forEach(function (ev) {
    window.addEventListener(ev, function () {
      try { window.parent.__bgmUnlock && window.parent.__bgmUnlock(); } catch (e) {}
    }, true);
  });

  // ----- Animation Loop -----
  var last = performance.now();
  function tick(now) {
    var dt = Math.min((now - last) / 1000, 0.1);
    last = now;
    var dy = 0, dx = 0;
    held.forEach(function (d) { dy += vecs[d][0]; dx += vecs[d][1]; });
    if (held.length) S.dir = held[held.length - 1];
    if (dx !== 0 || dy !== 0) {
      var n = Math.sqrt(dx * dx + dy * dy);
      var zoomSpeed = D.speed * Math.pow(2, D.baseZoom - map.getZoom());
      S.lat += dy / n * zoomSpeed * dt;
      S.lon += dx / n * zoomSpeed * dt / Math.cos(S.lat * Math.PI / 180);
      animT += dt;
      frame = Math.floor(animT / 0.1) % 4;
      marker.setLatLng([S.lat, S.lon]);
      map.setView([S.lat, S.lon], map.getZoom(), {animate: false});
    } else { animT = 0; frame = 0; }
    refreshIcon();
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
});
"""

icon_data = {
    "lat": START[0],
    "lon": START[1],
    "speed": SPEED,
    "baseZoom": 16,
    "labelA": LABEL_A,
    "labelB": LABEL_B,
    "banana": banana_uri(),
    "sprite": {
        d: [
            dict(zip(("url", "w", "h"), (driver_icon(d, k)[0], *driver_icon(d, k)[1])))
            for k in range(4)
        ]
        for d in DIRECTIONS
    },
}
js = PLAYER_JS.replace("__MAP__", m.get_name()).replace("__DATA__", json.dumps(icon_data))
m.get_root().script.add_child(folium.Element(js), name="player_js")

folium.LayerControl().add_to(m)

# ---------------------------------------------------------------------------
# Layout: large map on the left, all info on the right
# ---------------------------------------------------------------------------
with col_map:
    st.caption(
        "Click on the map, drive with W A S D (or Z Q S D), "
        "then press **Space**: 1st press = pickup, 2nd = dropoff, 3rd = new pickup. "
        "Press **B** to drop a banana 🍌."
    )
    # The HTML doesn't change when placing a point: the map doesn't reload
    components.html(m.get_root().render(), height=MAP_HEIGHT)

add_music()

with col_info:
    c1, c2 = st.columns(2)
    with c1:
        pickup_date = st.date_input("Date", value=date.today())
    with c2:
        pickup_time = st.time_input("Time", value=time(12, 0))
    passenger_count = st.number_input("Passengers", min_value=1, max_value=8, value=1, step=1)

    pickup_text = st.text_input(
        LABEL_A,
        placeholder="Space on map, or an address",
        help="Leave blank to use your GPS location.",
    )
    dropoff_text = st.text_input(LABEL_B, placeholder="Space on map, or an address")

    pickup = resolve(pickup_text)
    if pickup is None and not pickup_text.strip() and df is not None:
        pickup = (float(df["latitude"].iloc[0]), float(df["longitude"].iloc[0]), "Your location")
    dropoff = resolve(dropoff_text)

    if pickup_text.strip() and pickup is None:
        st.error("Pickup not found.")
    if dropoff_text.strip() and dropoff is None:
        st.error("Dropoff not found.")

    if pickup:
        st.markdown(f"**🔵 Pick up** — {pickup[2]}  \n`{pickup[0]:.5f}, {pickup[1]:.5f}`")
    if dropoff:
        st.markdown(f"**🟠 Drop off** — {dropoff[2]}  \n`{dropoff[0]:.5f}, {dropoff[1]:.5f}`")

    if pickup and dropoff:
        dt = datetime.combine(pickup_date, pickup_time).strftime("%Y-%m-%d %H:%M:%S")
        try:
            fare = predict_fare(
                dt, pickup[0], pickup[1], dropoff[0], dropoff[1], int(passenger_count)
            )
            game_fare(fare)
        except (requests.RequestException, KeyError, ValueError):
            st.error("Unable to get fare prediction.")
    else:
        st.info("Place A then B (Space) to see the fare.")
