import React, { useState } from "react";

// Parse a CSS declaration string ("a:b;c:d") into a React style object so the
// prototype's inline styles can be carried over near-verbatim.
export function s(css: string): React.CSSProperties {
  const o: Record<string, string> = {};
  css.split(";").forEach((decl) => {
    const i = decl.indexOf(":");
    if (i < 0) return;
    let k = decl.slice(0, i).trim();
    const v = decl.slice(i + 1).trim();
    if (!k) return;
    k = k.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    o[k] = v;
  });
  return o as React.CSSProperties;
}

type HvProps = {
  tag?: keyof JSX.IntrinsicElements;
  css: string;
  hover?: string;
  children?: React.ReactNode;
} & Omit<React.HTMLAttributes<HTMLElement>, "style">;

// Element with a hover style swap — replicates the prototype's `style-hover`.
export function Hv({ tag = "div", css, hover, children, ...rest }: HvProps) {
  const [h, setH] = useState(false);
  const Tag = tag as any;
  return (
    <Tag
      style={{ ...s(css), ...(h && hover ? s(hover) : {}) }}
      onMouseEnter={() => setH(true)}
      onMouseLeave={() => setH(false)}
      {...rest}
    >
      {children}
    </Tag>
  );
}

export const MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace";

export function dot(active: boolean): string {
  return `width:14px;height:14px;border-radius:50%;border:2px solid ${active ? "#7A2BF5" : "#D4D4E0"};background:${active ? "#7A2BF5" : "transparent"};box-shadow:inset 0 0 0 2px #fff;`;
}

export function box(checked: boolean): string {
  return `width:15px;height:15px;border-radius:4px;border:1.5px solid ${checked ? "#7A2BF5" : "#D4D4E0"};background:${checked ? "#7A2BF5" : "#fff"};color:#fff;font-size:10px;display:flex;align-items:center;justify-content:center;`;
}

export function fmtNum(n: number): string {
  return n.toLocaleString("en-US");
}
