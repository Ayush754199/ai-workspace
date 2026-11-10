import React from "react";

export const CallIcon: React.FC<{ type: "audio" | "video"; size?: number }> = ({ type, size = 18 }) => (
  type === "audio" ? <svg className="call-icon" width={size} height={size} viewBox="0 0 24 24" aria-hidden="true"><path d="M7.1 3.5 9.8 3c.7-.1 1.3.3 1.5.9l1.1 3.2c.2.6 0 1.2-.5 1.6L10 10.1c1 2 2.5 3.5 4.5 4.5l1.4-1.9c.4-.5 1-.7 1.6-.5l3.2 1.1c.6.2 1 .8.9 1.5l-.5 2.7c-.1.8-.8 1.4-1.6 1.4C10.4 18.9 5.1 13.6 5.1 4.5c0-.5.4-.9 1-1Z" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" /></svg>
    : <svg className="call-icon" width={size} height={size} viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="5" width="14" height="14" rx="2" fill="none" stroke="currentColor" strokeWidth="1.8" /><path d="m17 10 4-2v8l-4-2" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round" /></svg>
);
