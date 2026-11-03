import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { KeyRound, LogOut, ShieldCheck } from "lucide-react";
import { api, type LoginResult, type Session } from "../lib/api";
import { useStore } from "../lib/store";
import { Button, ErrorBox, Input, PageHeader } from "../components/ui";

type Mode = "sso" | "internal" | "cookie";

export default function Login() {
  const { session, setSession, refreshSession, toast } = useStore();
  const navigate = useNavigate();
  const [mode, setMode] = useState<Mode>("sso");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [jwt, setJwt] = useState("");
  const [remember, setRemember] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState<LoginResult | null>(null);
  const [stepValues, setStepValues] = useState<Record<string, string>>({});

  const finish = (r: LoginResult) => {
    setPassword(""); // never keep the password in memory longer than the request
    if (r.state === "authenticated") {
      setSession(r as Session);
      setStep(null);
      toast(`Signed in as ${r.display_name}`, "ok");
      navigate("/courses");
    } else if (r.state === "needs_input") {
      setStep(r);
      setStepValues({});
    } else {
      setError(r.message || "Sign-in failed.");
      setStep(null);
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (mode === "cookie") finish(await api.post<LoginResult>("/api/session/cookie", { jwt, remember }));
      else finish(await api.post<LoginResult>("/api/session/login", { method: mode, username, password, remember }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
      setJwt("");
    }
  };

  const submitStep = async (choice?: string) => {
    if (!step?.flow_id) return;
    setBusy(true);
    setError(null);
    try {
      finish(await api.post<LoginResult>("/api/session/continue", { flow_id: step.flow_id, values: stepValues, choice, remember }));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const logout = async () => {
    await api.post("/api/session/logout");
    await refreshSession();
    toast("Signed out. The saved session was deleted.");
  };

  if (session?.state === "authenticated")
    return (
      <div className="max-w-xl">
        <PageHeader title="TUM-Live connection" />
        <div className="rounded-lg border border-line bg-board-2 p-5">
          <div className="flex items-center gap-3">
            <ShieldCheck className="size-6 text-ok" aria-hidden />
            <div>
              <div className="font-semibold">Signed in as {session.display_name}</div>
              <div className="text-muted">Your session cookie is stored only on this computer. Your password is never saved.</div>
            </div>
          </div>
          <div className="mt-5 flex gap-2">
            <Button onClick={() => refreshSession(true)}>Check session</Button>
            <Button variant="danger" icon={<LogOut className="size-4" />} onClick={logout}>
              Sign out
            </Button>
          </div>
        </div>
      </div>
    );

  return (
    <div className="max-w-xl">
      <PageHeader
        title="Sign in to TUM-Live"
        sub={session?.state === "expired" ? "Your saved session has expired. Sign in again to keep downloading." : "Public courses work without signing in."}
      />

      {step ? (
        <div className="rounded-lg border border-line bg-board-2 p-5">
          <div className="mb-1 flex items-center gap-2 font-semibold">
            <KeyRound className="size-4 text-accent" aria-hidden /> TUM Login needs another step
          </div>
          <p className="mb-4 text-muted">{step.message || "Complete the step requested by the TUM identity provider."}</p>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              submitStep();
            }}
            className="flex flex-col gap-3"
          >
            {step.inputs?.map((i) => (
              <label key={i.name} className="flex flex-col gap-1">
                <span className="text-chalk-2">{i.label}</span>
                <Input
                  type={i.type === "password" ? "password" : "text"}
                  autoComplete="one-time-code"
                  value={stepValues[i.name] ?? ""}
                  onChange={(e) => setStepValues((v) => ({ ...v, [i.name]: e.target.value }))}
                  autoFocus
                />
              </label>
            ))}
            <div className="flex flex-wrap gap-2">
              {step.choices && step.choices.length > 1 ? (
                step.choices.map((c) => (
                  <Button key={c.name} type="button" variant={c.name.includes("proceed") ? "primary" : "outline"} busy={busy} onClick={() => submitStep(c.name)}>
                    {c.label}
                  </Button>
                ))
              ) : (
                <Button type="submit" variant="primary" busy={busy}>
                  Continue
                </Button>
              )}
              <Button type="button" variant="ghost" onClick={() => setStep(null)}>
                Start over
              </Button>
            </div>
          </form>
        </div>
      ) : (
        <form onSubmit={submit} className="rounded-lg border border-line bg-board-2 p-5">
          <div role="tablist" className="mb-5 flex gap-1 rounded-md bg-board p-1">
            {(
              [
                ["sso", "TUM Login"],
                ["internal", "TUM-Live account"],
                ["cookie", "Use browser session"],
              ] as [Mode, string][]
            ).map(([m, label]) => (
              <button
                key={m}
                type="button"
                role="tab"
                aria-selected={mode === m}
                onClick={() => setMode(m)}
                className={`h-8 flex-1 rounded text-[13px] font-medium ${mode === m ? "bg-board-3 text-chalk" : "text-muted hover:text-chalk"}`}
              >
                {label}
              </button>
            ))}
          </div>

          {mode !== "cookie" ? (
            <div className="flex flex-col gap-3">
              <p className="text-muted">
                {mode === "sso"
                  ? "Signs in through login.tum.de, the way your browser does. If TUM asks for a second factor, you'll enter it here."
                  : "For accounts created directly on TUM-Live (e.g. external participants)."}
              </p>
              <label className="flex flex-col gap-1">
                <span className="text-chalk-2">TUM ID or e-mail</span>
                <Input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" placeholder="ab12cde" required />
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-chalk-2">Password</span>
                <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
              </label>
            </div>
          ) : (
            <div className="flex flex-col gap-3">
              <p className="text-muted">
                Already signed in to live.rbg.tum.de in your browser (for example with a passkey)? Open the browser's developer tools, go to
                Storage / Application → Cookies → https://live.rbg.tum.de and copy the value of the <code className="text-chalk">jwt</code> cookie.
              </p>
              <label className="flex flex-col gap-1">
                <span className="text-chalk-2">Session cookie (jwt)</span>
                <Input value={jwt} onChange={(e) => setJwt(e.target.value)} placeholder="eyJhbGciOi…" required spellCheck={false} autoComplete="off" />
              </label>
            </div>
          )}

          <label className="mt-4 flex items-center gap-2 text-chalk-2">
            <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
            Keep me signed in on this computer (up to 7 days)
          </label>
          {error && (
            <div className="mt-4">
              <ErrorBox error={error} />
            </div>
          )}
          <div className="mt-5">
            <Button type="submit" variant="primary" busy={busy}>
              Sign in
            </Button>
          </div>
        </form>
      )}
      <p className="mt-4 text-[13px] text-muted">
        Credentials go only to TUM servers. They are not logged or stored; only the TUM-Live session cookie is kept, in the app's data folder.
      </p>
    </div>
  );
}
