import clsx from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";

type Variant = "primary" | "ghost" | "outline" | "danger";

export function Button({
  variant = "outline",
  size = "md",
  busy,
  icon,
  children,
  className,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: "sm" | "md";
  busy?: boolean;
  icon?: ReactNode;
}) {
  return (
    <button
      {...rest}
      disabled={rest.disabled || busy}
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-md font-medium whitespace-nowrap transition-colors",
        "disabled:cursor-not-allowed disabled:opacity-45",
        size === "sm" ? "h-7 px-2.5 text-[13px]" : "h-9 px-3.5 text-sm",
        variant === "primary" && "bg-accent text-accent-ink hover:brightness-110",
        variant === "outline" && "border border-line-strong text-chalk hover:bg-board-3",
        variant === "ghost" && "text-chalk-2 hover:bg-board-3 hover:text-chalk",
        variant === "danger" && "border border-bad/50 text-bad hover:bg-bad/10",
        className,
      )}
    >
      {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : icon}
      {children}
    </button>
  );
}

export function Badge({
  tone = "neutral",
  children,
  title,
}: {
  tone?: "neutral" | "accent" | "ok" | "bad" | "info" | "warn";
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex h-5 items-center gap-1 rounded px-1.5 text-[12px] font-medium whitespace-nowrap",
        tone === "neutral" && "bg-board-3 text-chalk-2",
        tone === "accent" && "bg-accent/15 text-accent",
        tone === "ok" && "bg-ok/15 text-ok",
        tone === "bad" && "bg-bad/15 text-bad",
        tone === "info" && "bg-info/15 text-info",
        tone === "warn" && "bg-warn/15 text-warn",
      )}
    >
      {children}
    </span>
  );
}

/** Real progress when `value` is a number, an indeterminate sweep when it's null. */
export function ProgressBar({ value, tone = "accent", className }: { value: number | null; tone?: "accent" | "ok" | "bad"; className?: string }) {
  const color = tone === "ok" ? "bg-ok" : tone === "bad" ? "bg-bad" : "bg-accent";
  if (value == null)
    return (
      <div
        role="progressbar"
        aria-label="In progress, amount unknown"
        className={clsx("indeterminate h-1.5 rounded-full bg-board-3", className)}
      />
    );
  const pct = Math.max(0, Math.min(100, value * 100));
  return (
    <div
      role="progressbar"
      aria-valuenow={Math.round(pct)}
      aria-valuemin={0}
      aria-valuemax={100}
      className={clsx("h-1.5 overflow-hidden rounded-full bg-board-3", className)}
    >
      <div className={clsx("h-full rounded-full transition-[width] duration-500", color)} style={{ width: `${pct}%` }} />
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("skeleton", className)} aria-hidden />;
}

export function Empty({ icon, title, children }: { icon?: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-line px-6 py-14 text-center">
      {icon && <div className="text-muted">{icon}</div>}
      <p className="text-[15px] font-semibold">{title}</p>
      {children && <div className="max-w-md text-muted">{children}</div>}
    </div>
  );
}

export function ErrorBox({ error, action }: { error: string; action?: ReactNode }) {
  return (
    <div role="alert" className="flex items-start gap-3 rounded-lg border border-bad/40 bg-bad/8 px-4 py-3">
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-bad" aria-hidden />
      <div className="flex-1 text-chalk-2">{error}</div>
      {action}
    </div>
  );
}

export function PageHeader({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-[22px] font-semibold tracking-[-0.01em]">{title}</h1>
        {sub && <div className="mt-0.5 text-muted">{sub}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={clsx(
        "h-9 rounded-md border border-line-strong bg-board-2 px-3 text-chalk placeholder:text-muted",
        "focus:border-accent focus:outline-none",
        props.className,
      )}
    />
  );
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={clsx(
        "h-9 rounded-md border border-line-strong bg-board-2 px-2.5 text-chalk focus:border-accent focus:outline-none",
        props.className,
      )}
    />
  );
}
