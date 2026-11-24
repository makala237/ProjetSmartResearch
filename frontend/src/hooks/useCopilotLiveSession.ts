import { useCallback, useEffect, useRef, useState } from "react";
import { getCopilotLiveWsUrl } from "../api/client";
import type { ModuleKey } from "../types/auth";

export type LiveStatus = "idle" | "connecting" | "connected" | "error";

export interface LiveTranscriptItem {
  id: string;
  role: "user" | "assistant";
  text: string;
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

function base64ToUint8Array(b64: string): Uint8Array {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) {
    bytes[i] = bin.charCodeAt(i);
  }
  return bytes;
}

function downsampleTo16kPcm16(input: Float32Array, inputRate: number): Int16Array {
  if (inputRate === 16000) {
    const out = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) {
      const s = Math.max(-1, Math.min(1, input[i]));
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    return out;
  }
  const ratio = inputRate / 16000;
  const outLength = Math.max(1, Math.floor(input.length / ratio));
  const out = new Int16Array(outLength);
  for (let i = 0; i < outLength; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    let count = 0;
    for (let j = start; j < end; j++) {
      sum += input[j];
      count++;
    }
    const avg = count > 0 ? sum / count : 0;
    const s = Math.max(-1, Math.min(1, avg));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

export function useCopilotLiveSession(
  projectId: string,
  activeModule: ModuleKey,
  activeSectionId?: string
) {
  const [status, setStatus] = useState<LiveStatus>("idle");
  const [activeLiveModel, setActiveLiveModel] = useState<string>("gemini-3.8-live");
  const [screenSharing, setScreenSharing] = useState(false);
  const [liveTranscripts, setLiveTranscripts] = useState<LiveTranscriptItem[]>([]);
  const [liveError, setLiveError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const micStreamRef = useRef<MediaStream | null>(null);
  const inputCtxRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const outputCtxRef = useRef<AudioContext | null>(null);
  const nextPlayTimeRef = useRef<number>(0);
  const activeSourcesRef = useRef<Set<AudioBufferSourceNode>>(new Set());

  const screenStreamRef = useRef<MediaStream | null>(null);
  const screenTimerRef = useRef<number | null>(null);

  const stopPlayback = useCallback(() => {
    for (const src of activeSourcesRef.current) {
      try {
        src.stop();
      } catch {
        /* already stopped */
      }
    }
    activeSourcesRef.current.clear();
    if (outputCtxRef.current) {
      nextPlayTimeRef.current = outputCtxRef.current.currentTime;
    }
  }, []);

  const stopScreenShare = useCallback(() => {
    if (screenTimerRef.current !== null) {
      window.clearInterval(screenTimerRef.current);
      screenTimerRef.current = null;
    }
    if (screenStreamRef.current) {
      screenStreamRef.current.getTracks().forEach((t) => t.stop());
      screenStreamRef.current = null;
    }
    setScreenSharing(false);
  }, []);

  const stopLive = useCallback(() => {
    stopScreenShare();
    stopPlayback();

    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current = null;
    }
    if (micStreamRef.current) {
      micStreamRef.current.getTracks().forEach((t) => t.stop());
      micStreamRef.current = null;
    }
    if (inputCtxRef.current) {
      inputCtxRef.current.close().catch(() => undefined);
      inputCtxRef.current = null;
    }
    if (outputCtxRef.current) {
      outputCtxRef.current.close().catch(() => undefined);
      outputCtxRef.current = null;
    }
    if (wsRef.current) {
      try {
        if (wsRef.current.readyState === WebSocket.OPEN) {
          wsRef.current.send(JSON.stringify({ type: "stop" }));
        }
        wsRef.current.close();
      } catch {
        /* ignore close errors */
      }
      wsRef.current = null;
    }
    setStatus("idle");
  }, [stopPlayback, stopScreenShare]);

  const enqueuePcm24kAudio = useCallback((base64Data: string) => {
    if (!outputCtxRef.current) {
      const Ctx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      outputCtxRef.current = new Ctx({ sampleRate: 24000 });
      nextPlayTimeRef.current = outputCtxRef.current.currentTime;
    }
    const ctx = outputCtxRef.current;
    if (ctx.state === "suspended") {
      ctx.resume().catch(() => undefined);
    }

    const bytes = base64ToUint8Array(base64Data);
    const sampleCount = Math.floor(bytes.byteLength / 2);
    if (sampleCount <= 0) return;

    const pcm = new Int16Array(bytes.buffer, bytes.byteOffset, sampleCount);
    const floatBuf = new Float32Array(sampleCount);
    for (let i = 0; i < sampleCount; i++) {
      floatBuf[i] = pcm[i] / 32768;
    }

    const audioBuffer = ctx.createBuffer(1, sampleCount, 24000);
    audioBuffer.getChannelData(0).set(floatBuf);

    const src = ctx.createBufferSource();
    src.buffer = audioBuffer;
    src.connect(ctx.destination);
    activeSourcesRef.current.add(src);
    src.onended = () => {
      activeSourcesRef.current.delete(src);
    };

    const startAt = Math.max(ctx.currentTime + 0.02, nextPlayTimeRef.current);
    src.start(startAt);
    nextPlayTimeRef.current = startAt + audioBuffer.duration;
  }, []);

  const appendTranscript = useCallback((role: "user" | "assistant", text: string) => {
    const clean = text.trim();
    if (!clean) return;
    setLiveTranscripts((prev) => {
      const last = prev[prev.length - 1];
      if (last && last.role === role) {
        return [
          ...prev.slice(0, -1),
          { ...last, text: `${last.text} ${clean}`.trim() },
        ];
      }
      return [
        ...prev.slice(-15),
        { id: `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`, role, text: clean },
      ];
    });
  }, []);

  const startLive = useCallback(async () => {
    if (status === "connecting" || status === "connected") return;
    setLiveError(null);
    setLiveTranscripts([]);
    setStatus("connecting");

    try {
      const micStream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      micStreamRef.current = micStream;

      const wsUrl = getCopilotLiveWsUrl(projectId, activeModule, activeSectionId);
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(String(ev.data || "{}"));
          if (msg.type === "ready") {
            if (msg.model_used) setActiveLiveModel(String(msg.model_used));
            setStatus("connected");
          } else if (msg.type === "audio" && msg.data) {
            enqueuePcm24kAudio(String(msg.data));
          } else if (msg.type === "interrupted") {
            stopPlayback();
          } else if (msg.type === "transcript" && msg.text) {
            appendTranscript(msg.role === "user" ? "user" : "assistant", String(msg.text));
          } else if (msg.type === "error") {
            setLiveError(String(msg.message || "Erreur Live Audio."));
            stopLive();
            setStatus("error");
          }
        } catch {
          /* ignore malformed JSON frame */
        }
      };

      ws.onerror = () => {
        setLiveError("Connexion Live interrompue.");
        stopLive();
        setStatus("error");
      };

      ws.onclose = () => {
        if (wsRef.current === ws) {
          stopLive();
        }
      };

      const Ctx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const inCtx = new Ctx();
      inputCtxRef.current = inCtx;
      const source = inCtx.createMediaStreamSource(micStream);
      const processor = inCtx.createScriptProcessor(4096, 1, 1);
      processorRef.current = processor;

      processor.onaudioprocess = (e) => {
        if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
        const floatIn = e.inputBuffer.getChannelData(0);
        const pcm16 = downsampleTo16kPcm16(floatIn, inCtx.sampleRate);
        const b64 = arrayBufferToBase64(pcm16.buffer as ArrayBuffer);
        wsRef.current.send(
          JSON.stringify({
            type: "audio",
            data: b64,
            mime_type: "audio/pcm;rate=16000",
          })
        );
      };

      source.connect(processor);
      processor.connect(inCtx.destination);
    } catch (err) {
      setLiveError(
        err instanceof Error ? err.message : "Accès au microphone impossible."
      );
      stopLive();
      setStatus("error");
    }
  }, [
    status,
    projectId,
    activeModule,
    activeSectionId,
    enqueuePcm24kAudio,
    stopPlayback,
    appendTranscript,
    stopLive,
  ]);

  const toggleScreenShare = useCallback(async () => {
    if (screenSharing) {
      stopScreenShare();
      return;
    }
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return;
    try {
      const displayStream = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: 2 },
        audio: false,
      });
      screenStreamRef.current = displayStream;
      setScreenSharing(true);

      const videoEl = document.createElement("video");
      videoEl.srcObject = displayStream;
      videoEl.muted = true;
      await videoEl.play();

      const canvas = document.createElement("canvas");
      const sendFrame = () => {
        if (
          !wsRef.current ||
          wsRef.current.readyState !== WebSocket.OPEN ||
          videoEl.videoWidth <= 0
        ) {
          return;
        }
        const scale = Math.min(1, 1024 / videoEl.videoWidth);
        canvas.width = Math.max(1, Math.floor(videoEl.videoWidth * scale));
        canvas.height = Math.max(1, Math.floor(videoEl.videoHeight * scale));
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        ctx.drawImage(videoEl, 0, 0, canvas.width, canvas.height);
        const dataUrl = canvas.toDataURL("image/jpeg", 0.65);
        const commaIdx = dataUrl.indexOf(",");
        if (commaIdx > -1) {
          wsRef.current.send(
            JSON.stringify({
              type: "video",
              data: dataUrl.slice(commaIdx + 1),
              mime_type: "image/jpeg",
            })
          );
        }
      };

      displayStream.getVideoTracks()[0]?.addEventListener("ended", () => {
        stopScreenShare();
      });

      sendFrame();
      screenTimerRef.current = window.setInterval(sendFrame, 1500);
    } catch {
      stopScreenShare();
    }
  }, [screenSharing, stopScreenShare]);

  const updateLiveContext = useCallback((mod: ModuleKey, sid?: string) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: "context",
          active_module: mod,
          active_section_id: sid || "",
        })
      );
    }
  }, []);

  useEffect(() => {
    return () => {
      stopLive();
    };
  }, [stopLive]);

  return {
    status,
    activeLiveModel,
    screenSharing,
    liveTranscripts,
    liveError,
    startLive,
    stopLive,
    toggleScreenShare,
    updateLiveContext,
  };
}
