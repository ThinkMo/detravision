import { useEffect, useState } from "react";

export type JpegStreamState = "idle" | "connecting" | "connected" | "reconnecting";

export function useJpegStream(cameraId: number, enabled: boolean) {
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const [state, setState] = useState<JpegStreamState>("idle");

  useEffect(() => {
    if (!enabled) {
      setState("idle");
      setFrameUrl(null);
      return;
    }

    let disposed = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let retry = 0;
    let currentObjectUrl: string | null = null;

    const releaseFrame = () => {
      if (currentObjectUrl) {
        URL.revokeObjectURL(currentObjectUrl);
        currentObjectUrl = null;
      }
    };

    const connect = () => {
      if (disposed) return;
      setState(retry === 0 ? "connecting" : "reconnecting");
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${protocol}//${window.location.host}/ws/live/${cameraId}`);
      socket.binaryType = "blob";
      socket.onopen = () => {
        retry = 0;
        setState("connected");
      };
      socket.onmessage = (message) => {
        const blob = message.data instanceof Blob ? message.data : new Blob([message.data]);
        const nextUrl = URL.createObjectURL(blob);
        releaseFrame();
        currentObjectUrl = nextUrl;
        setFrameUrl(nextUrl);
      };
      socket.onclose = () => {
        if (disposed) return;
        retry += 1;
        setState("reconnecting");
        const delay = Math.min(1_000 * (2 ** (retry - 1)), 30_000);
        reconnectTimer = setTimeout(connect, delay);
      };
      socket.onerror = () => socket?.close();
    };

    connect();
    return () => {
      disposed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (socket) {
        socket.onopen = null;
        socket.onmessage = null;
        socket.onclose = null;
        socket.onerror = null;
        socket.close();
      }
      releaseFrame();
    };
  }, [cameraId, enabled]);

  return { frameUrl, state };
}
