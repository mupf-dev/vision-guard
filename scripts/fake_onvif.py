"""Simuliertes ONVIF-PTZ-Gerät für Tests ohne echte Schwenk-Kamera (siehe AGENTS.md, „PTZ“).

Protokolliert jeden Aufruf (Operation, Geschwindigkeit/Timeout, Preset), verlangt WS-Security außer bei
GetSystemDateAndTime und braucht 2 s für GotoPreset. Das Videobild bewegt sich natürlich nicht.
"""
import re, sys, time, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
presets = {"1": "Grundposition", "2": "Tür", "3": "Fenster"}
lock = threading.Lock()
NS = 'xmlns:tt="http://www.onvif.org/ver10/schema" xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl" xmlns:tds="http://www.onvif.org/ver10/device/wsdl" xmlns:trt="http://www.onvif.org/ver10/media/wsdl"'
def env(body): return f'<?xml version="1.0"?><s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope" {NS}><s:Body>{body}</s:Body></s:Envelope>'
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        data = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        m = re.search(r"<s:Body>\s*<(\w+)", data); op = m.group(1) if m else "?"
        auth = "Security" in data
        host = self.headers.get("Host")
        extra = ""
        if op == "ContinuousMove":
            extra = " ".join(re.findall(r'(x="[^"]+" y="[^"]+"|<Timeout>[^<]+)', data))
        if op in ("GotoPreset", "SetPreset", "RemovePreset"):
            extra = " ".join(re.findall(r"<Preset(?:Token|Name)>([^<]+)<", data))
        print(f"{time.strftime('%H:%M:%S')} {op} {extra} auth={auth}", flush=True)
        if op != "GetSystemDateAndTime" and not auth:
            return self.reply(env('<s:Fault><s:Reason><s:Text>Sender not Authorized</s:Text></s:Reason></s:Fault>'), 400)
        if op == "GetSystemDateAndTime":
            t = time.gmtime()
            b = f'<tds:GetSystemDateAndTimeResponse><tds:SystemDateAndTime><tt:UTCDateTime><tt:Time><tt:Hour>{t.tm_hour}</tt:Hour><tt:Minute>{t.tm_min}</tt:Minute><tt:Second>{t.tm_sec}</tt:Second></tt:Time><tt:Date><tt:Year>{t.tm_year}</tt:Year><tt:Month>{t.tm_mon}</tt:Month><tt:Day>{t.tm_mday}</tt:Day></tt:Date></tt:UTCDateTime></tds:SystemDateAndTime></tds:GetSystemDateAndTimeResponse>'
        elif op == "GetDeviceInformation":
            b = '<tds:GetDeviceInformationResponse><tds:Manufacturer>Fake</tds:Manufacturer><tds:Model>PTZ-Sim</tds:Model><tds:FirmwareVersion>1.0</tds:FirmwareVersion></tds:GetDeviceInformationResponse>'
        elif op == "GetCapabilities":
            b = f'<tds:GetCapabilitiesResponse><tds:Capabilities><tt:Media><tt:XAddr>http://192.0.2.1/onvif/media_service</tt:XAddr></tt:Media><tt:PTZ><tt:XAddr>http://192.0.2.1/onvif/ptz_service</tt:XAddr></tt:PTZ></tds:Capabilities></tds:GetCapabilitiesResponse>'
        elif op == "GetProfiles":
            b = '<trt:GetProfilesResponse><trt:Profiles token="main"><tt:Name>main</tt:Name><tt:PTZConfiguration token="ptz0"/></trt:Profiles></trt:GetProfilesResponse>'
        elif op == "GetNodes":
            b = '<tptz:GetNodesResponse><tptz:PTZNode token="n0"><tt:SupportedPTZSpaces><tt:ContinuousPanTiltVelocitySpace/><tt:ContinuousZoomVelocitySpace/></tt:SupportedPTZSpaces><tt:MaximumNumberOfPresets>16</tt:MaximumNumberOfPresets></tptz:PTZNode></tptz:GetNodesResponse>'
        elif op == "GetPresets":
            with lock: ps = "".join(f'<tptz:Preset token="{k}"><tt:Name>{v}</tt:Name></tptz:Preset>' for k, v in presets.items())
            b = f'<tptz:GetPresetsResponse>{ps}</tptz:GetPresetsResponse>'
        elif op == "SetPreset":
            name = re.search(r"<PresetName>([^<]+)<", data).group(1)
            with lock: tok = str(max(map(int, presets)) + 1 if presets else 1); presets[tok] = name
            b = f'<tptz:SetPresetResponse><tptz:PresetToken>{tok}</tptz:PresetToken></tptz:SetPresetResponse>'
        elif op == "RemovePreset":
            tok = re.search(r"<PresetToken>([^<]+)<", data).group(1)
            with lock: presets.pop(tok, None)
            b = '<tptz:RemovePresetResponse/>'
        elif op == "GotoPreset":
            time.sleep(2); b = '<tptz:GotoPresetResponse/>'
        elif op == "GetStatus":
            b = '<tptz:GetStatusResponse><tptz:PTZStatus><tt:MoveStatus><tt:PanTilt>IDLE</tt:PanTilt><tt:Zoom>IDLE</tt:Zoom></tt:MoveStatus></tptz:PTZStatus></tptz:GetStatusResponse>'
        else:
            b = f'<tptz:{op}Response/>'
        self.reply(env(b))
    def reply(self, text, code=200):
        raw = text.encode(); self.send_response(code); self.send_header("Content-Type", "application/soap+xml"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
