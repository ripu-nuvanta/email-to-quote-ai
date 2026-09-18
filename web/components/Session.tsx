"use client";

import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, storage } from "@/lib/api";
import type { AppConfig } from "@/lib/types";

interface SessionValue {
  ready: boolean;
  token: string;
  name: string;
  config: AppConfig | null;
  connectionError: string | null;
  setToken: (token: string) => void;
  setName: (name: string) => void;
}

const SessionContext = createContext<SessionValue | null>(null);

/** Remembers the salesperson's name and approver token in this browser, and loads the app settings. */
export function SessionProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [token, setTokenState] = useState("");
  const [name, setNameState] = useState("");
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  useEffect(() => {
    setTokenState(storage.get("approverToken"));
    setNameState(storage.get("approverName"));
    setReady(true);
  }, []);

  useEffect(() => {
    if (!ready) return;
    setConfig(null);
    setConnectionError(null);
    if (!token) return;
    let cancelled = false;
    api<AppConfig>("/api/config", token)
      .then((value) => {
        if (!cancelled) setConfig(value);
      })
      .catch((error: Error) => {
        if (!cancelled) setConnectionError(error.message);
      });
    return () => {
      cancelled = true;
    };
  }, [ready, token]);

  const setToken = useCallback((value: string) => {
    storage.set("approverToken", value);
    setTokenState(value);
  }, []);

  const setName = useCallback((value: string) => {
    storage.set("approverName", value);
    setNameState(value);
  }, []);

  return (
    <SessionContext.Provider value={{ ready, token, name, config, connectionError, setToken, setName }}>
      {children}
    </SessionContext.Provider>
  );
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSession must be used inside SessionProvider");
  return value;
}
