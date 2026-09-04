import { useState } from "react";
import { steerSession, stopSession } from "../api";

interface Props {
  sessionId: string;
  isRunning: boolean;
}

export default function SteeringChat({ sessionId, isRunning }: Props) {
  const [message, setMessage] = useState("");
  const [sending, setSending] = useState(false);

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    if (!message.trim() || !isRunning) return;
    setSending(true);
    try {
      await steerSession(sessionId, message.trim());
      setMessage("");
    } catch (err) {
      alert(String(err));
    } finally {
      setSending(false);
    }
  }

  async function handleStop() {
    try {
      await stopSession(sessionId);
    } catch (err) {
      alert(String(err));
    }
  }

  return (
    <form className="steering-bar" onSubmit={handleSend}>
      <input
        value={message}
        onChange={(e) => setMessage(e.target.value)}
        placeholder={
          isRunning
            ? "Steer the agent: 'try a different approach', 'focus on tests'..."
            : "Session not running"
        }
        disabled={!isRunning || sending}
      />
      <button
        type="submit"
        className="btn-primary"
        disabled={!isRunning || sending || !message.trim()}
      >
        Send
      </button>
      <button
        type="button"
        className="btn-danger"
        disabled={!isRunning}
        onClick={handleStop}
      >
        Stop
      </button>
    </form>
  );
}
