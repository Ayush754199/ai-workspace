import React from "react";
import type { ChatMessage } from "../api/chatApi";

type Props = { message: ChatMessage; own: boolean };

const MessageBubble: React.FC<Props> = ({ message, own }) => {
  const roleTitle = (message.sender_role || "user").charAt(0).toUpperCase() + (message.sender_role || "user").slice(1);
  return (
    <div className={`message-row ${own ? "own" : ""}`}>
      <div className="message-bubble">
        <small className="sender-tag">{message.sender_name} ({roleTitle})</small>
        <p>{message.content}</p>
        {message.created_at && <time>{new Date(message.created_at).toLocaleString()}</time>}
      </div>
    </div>
  );
};

export default MessageBubble;
