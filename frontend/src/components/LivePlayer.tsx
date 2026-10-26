import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { VideoOff, Loader2 } from "lucide-react";
import { isMock, mockSnapshot } from "../lib/mock";
import { useMsgs } from "../i18n";

/** Bereich innerhalb des Containers, in dem das Videobild tatsächlich liegt (object-fit: contain). */
export interface ContentRect { x: number; y: number; w: number; h: number; cw: number; ch: number }

interface VideoStreamEl extends HTMLElement {
  mode: string;
  media: string;
  background: boolean;
  src: string;
  video?: HTMLVideoElement;
  ondisconnect?: () => void;
}

/** Letterbox-Berechnung für object-fit: contain */
export function containRect(cw: number, ch: number, vw: number, vh: number): ContentRect {
  if (!cw || !ch || !vw || !vh) return { x: 0, y: 0, w: cw, h: ch, cw, ch };
  const scale = Math.min(cw / vw, ch / vh);
  const w = vw * scale, h = vh * scale;
  return { x: (cw - w) / 2, y: (ch - h) / 2, w, h, cw, ch };
}

/**
 * Live-Video über go2rtc (<video-stream>), imperativ erzeugt.
 * `aspect` (Breite/Höhe) dient als Fallback, solange das Video noch keine Größe meldet (z. B. MJPEG-Modus).
 * Kinder werden als Render-Prop mit dem tatsächlichen Bildbereich aufgerufen (für Overlays).
 */
export function LivePlayer({
  cameraId, aspect, offline, children,
}: { cameraId: number; aspect?: number | null; offline?: boolean; children?: (r: ContentRect) => ReactNode }) {
  const m = useMsgs();
  const wrapRef = useRef<HTMLDivElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });
  const [vsize, setVsize] = useState({ w: 0, h: 0 });
  const [state, setState] = useState<"loading" | "playing" | "error">("loading");

  // Containergröße beobachten
  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setBox({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setBox({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  // Player erzeugen
  useEffect(() => {
    if (isMock || offline) return;
    const host = hostRef.current;
    if (!host) return;
    setState("loading");
    setVsize({ w: 0, h: 0 });
    let el: VideoStreamEl | null = null;
    let video: HTMLVideoElement | undefined;
    let cancelled = false;
    const onSize = () => {
      if (video && video.videoWidth) setVsize({ w: video.videoWidth, h: video.videoHeight });
    };
    const onPlaying = () => { setState("playing"); onSize(); };
    const posterObs = new MutationObserver(() => {
      // MJPEG-Modus: Bild kommt als poster
      if (video?.poster) {
        setState("playing");
        const img = new Image();
        img.onload = () => setVsize({ w: img.naturalWidth, h: img.naturalHeight });
        img.src = video.poster;
        posterObs.disconnect();
      }
    });
    const create = () => {
      if (cancelled) return;
      el = document.createElement("video-stream") as VideoStreamEl;
      el.mode = "webrtc,mse,mjpeg";
      el.media = "video";
      el.background = false;
      el.setAttribute("mode", "webrtc,mse,mjpeg");
      host.appendChild(el); // -> oninit erzeugt <video>
      video = el.video;
      if (video) {
        video.controls = false;
        video.muted = true;
        video.playsInline = true;
        video.addEventListener("loadedmetadata", onSize);
        video.addEventListener("resize", onSize);
        video.addEventListener("playing", onPlaying);
        posterObs.observe(video, { attributes: true, attributeFilter: ["poster"] });
      }
      el.src = `/go2rtc/api/ws?src=cam_${cameraId}`;
    };
    if (customElements.get("video-stream")) create();
    else customElements.whenDefined("video-stream").then(create);
    const errTimer = window.setTimeout(() => setState((s) => (s === "loading" ? "error" : s)), 20000);
    return () => {
      cancelled = true;
      window.clearTimeout(errTimer);
      posterObs.disconnect();
      if (video) {
        video.removeEventListener("loadedmetadata", onSize);
        video.removeEventListener("resize", onSize);
        video.removeEventListener("playing", onPlaying);
      }
      if (el) {
        try { el.ondisconnect?.(); } catch { /* ignore */ }
        el.remove();
      }
    };
  }, [cameraId, offline]);

  const vw = vsize.w || (aspect ? aspect * 1000 : 16000);
  const vh = vsize.h || (aspect ? 1000 : 9000);
  const rect = containRect(box.w, box.h, vw, vh);
  const mockSrc = isMock ? mockSnapshot(cameraId) : null;

  return (
    <div className="player" ref={wrapRef}>
      {mockSrc ? (
        <img className="player-img" src={mockSrc} alt="" onLoad={(e) => { setVsize({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight }); setState("playing"); }} />
      ) : (
        <div className="player-host" ref={hostRef} />
      )}
      {offline ? (
        <div className="player-state offline">
          <div className="inner"><VideoOff size={32} /><span>{m.ui.player.disabled}</span></div>
        </div>
      ) : state !== "playing" && (
        <div className="player-state">
          <div className="inner">
            {state === "loading" ? <><Loader2 size={26} style={{ animation: "spin 1s linear infinite" }} /><span>{m.ui.player.connecting}</span></>
              : <><VideoOff size={28} /><span>{m.ui.player.unavailable}</span></>}
          </div>
        </div>
      )}
      {children && box.w > 0 && children(rect)}
    </div>
  );
}
