import { MESSAGE_LIMIT } from "../../reply/types"

type Props = {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void
}

export default function MessageBox({ value, onChange, onSubmit }: Props) {
  const atLimit = value.length >= MESSAGE_LIMIT

  return (
    <form
      className="rounded-xl bg-white p-4 shadow-sm"
      onSubmit={(e) => {
        e.preventDefault()
        onSubmit()
      }}
    >
      <label htmlFor="reply-message" className="mb-1 block font-medium">
        Message to reply to
      </label>
      <textarea
        id="reply-message"
        value={value}
        maxLength={MESSAGE_LIMIT}
        rows={4}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
            e.preventDefault()
            onSubmit()
          }
        }}
        aria-describedby="reply-message-hint reply-message-count"
        className="w-full rounded-md border px-3 py-2"
        placeholder="Paste the message you want to answer with a meme"
      />
      <div className="mt-1 flex items-center justify-between text-sm">
        <span id="reply-message-hint" className="text-gray-500">Press Ctrl+Enter to search.</span>
        <span id="reply-message-count" className={atLimit ? "font-medium text-red-600" : "text-gray-500"}>
          {value.length}/{MESSAGE_LIMIT}
        </span>
      </div>
      <p role="status" className="sr-only">{atLimit ? `Limit of ${MESSAGE_LIMIT} characters reached` : ""}</p>
      <button
        type="submit"
        disabled={value.trim() === ""}
        className="mt-3 min-h-11 rounded-md bg-blue-600 px-4 py-2 font-medium text-white disabled:opacity-50"
      >
        Find a meme
      </button>
    </form>
  )
}
