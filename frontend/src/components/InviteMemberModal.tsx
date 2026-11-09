import React, { useEffect, useState } from "react";
import type { ChatUser } from "../api/chatApi";

type Props = { onClose: () => void; onSubmit: (identifier: string) => Promise<string | null>; onCreateLink: () => Promise<string | null>; onSearchUsers: (query: string) => Promise<ChatUser[]> };

const InviteMemberModal: React.FC<Props> = ({ onClose, onSubmit, onCreateLink, onSearchUsers }) => {
  const [identifier, setIdentifier] = useState("");
  const [matches, setMatches] = useState<ChatUser[]>([]);
  const [selectedUser, setSelectedUser] = useState<ChatUser | null>(null);
  const [error, setError] = useState("");
  const [isSaving, setIsSaving] = useState(false);
  const [inviteLink, setInviteLink] = useState("");
  const [linkError, setLinkError] = useState("");

  useEffect(() => {
    if (selectedUser || identifier.trim().length < 1) { setMatches([]); return; }
    const timer = window.setTimeout(() => {
      void onSearchUsers(identifier.trim()).then(setMatches).catch(() => setMatches([]));
    }, 220);
    return () => window.clearTimeout(timer);
  }, [identifier, onSearchUsers, selectedUser]);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!identifier.trim()) return;
    setIsSaving(true);
    setError("");
    const result = await onSubmit(selectedUser?.email || identifier.trim());
    setIsSaving(false);
    if (result) setError(result);
    else onClose();
  };

  const createLink = async () => {
    setLinkError("");
    const token = await onCreateLink();
    if (token) setInviteLink(`${window.location.origin}/dashboard?invite=${encodeURIComponent(token)}`);
    else setLinkError("Unable to create invite link");
  };

  return <div className="modal-backdrop" role="presentation"><form className="invite-modal" onSubmit={submit}><h2>Invite member</h2><p>Type a name or email, then select the user.</p><div className="invite-user-picker"><input autoFocus value={identifier} onChange={(event) => { setIdentifier(event.target.value); setSelectedUser(null); }} placeholder="Search by name or email" aria-label="Search users" autoComplete="off" />{matches.length > 0 && <div className="invite-user-options" role="listbox">{matches.map((match) => <button type="button" role="option" key={match.id} onClick={() => { setSelectedUser(match); setIdentifier(match.email); setMatches([]); }}><strong>{match.name || match.email}</strong><small>{match.email}</small></button>)}</div>}</div>{selectedUser && <p className="invite-selected">Selected: {selectedUser.name || selectedUser.email}</p>}{error && <p className="error-text">{error}</p>}<div className="modal-actions"><button type="button" onClick={createLink} disabled={isSaving}>Create invite link</button><button type="button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={isSaving || !identifier.trim()}>{isSaving ? "Inviting..." : "Invite"}</button></div>{inviteLink && <div className="invite-link-result"><input readOnly value={inviteLink} aria-label="Room invite link" /><button type="button" onClick={() => void navigator.clipboard.writeText(inviteLink)}>Copy link</button></div>}{linkError && <p className="error-text">{linkError}</p>}</form></div>;
};

export default InviteMemberModal;
