import React, { useState } from "react";

type Props = { onClose: () => void; onSubmit: (identifier: string) => Promise<string | null> };

const InviteMemberModal: React.FC<Props> = ({ onClose, onSubmit }) => {
  const [identifier, setIdentifier] = useState("");
  const [error, setError] = useState("");
  const [isSaving, setIsSaving] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!identifier.trim()) return;
    setIsSaving(true);
    setError("");
    const result = await onSubmit(identifier.trim());
    setIsSaving(false);
    if (result) setError(result);
    else onClose();
  };

  return <div className="modal-backdrop" role="presentation"><form className="invite-modal" onSubmit={submit}><h2>Invite member</h2><p>Use the member's exact username or email.</p><input autoFocus value={identifier} onChange={(event) => setIdentifier(event.target.value)} placeholder="username or email" />{error && <p className="error-text">{error}</p>}<div className="modal-actions"><button type="button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={isSaving || !identifier.trim()}>{isSaving ? "Inviting..." : "Invite"}</button></div></form></div>;
};

export default InviteMemberModal;
