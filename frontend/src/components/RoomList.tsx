import React from "react";
import type { ChatRoom } from "../api/chatApi";

type Props = { rooms: ChatRoom[]; selectedId: number | null; onSelect: (room: ChatRoom) => void };

const RoomList: React.FC<Props> = ({ rooms, selectedId, onSelect }) => (
  <aside className="room-list" aria-label="Your chat rooms">
    <div className="room-list-heading"><span>Your rooms</span><strong>{rooms.length}</strong></div>
    {rooms.length === 0 && <p className="muted-text">No rooms available yet.</p>}
    {rooms.map((room) => <button className={`room-item ${selectedId === room.id ? "selected" : ""}`} key={room.id} onClick={() => onSelect(room)}><strong>{room.name}</strong><small>{room.room_type} · {room.member_details.length} members</small></button>)}
  </aside>
);

export default RoomList;
