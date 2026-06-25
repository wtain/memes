import { useCallback, useRef, useState } from "react"
import type { MemesApi } from "../api/MemesApi"
import type { UploadResponse } from "../types/upload"

const ACCEPTED_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp", "image/tiff"]
const MAX_FILE_SIZE = 20 * 1024 * 1024
const MAX_FILES = 50

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

interface Props {
  memesApi: MemesApi
}

export default function UploadPage({ memesApi }: Props) {
  const [files, setFiles] = useState<File[]>([])
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState<UploadResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const addFiles = useCallback((incoming: FileList | File[]) => {
    setResult(null)
    setError(null)
    setFiles(prev => {
      const combined = [...prev]
      for (const f of Array.from(incoming)) {
        if (combined.length >= MAX_FILES) break
        if (!combined.some(e => e.name === f.name && e.size === f.size)) {
          combined.push(f)
        }
      }
      return combined.slice(0, MAX_FILES)
    })
  }, [])

  const removeFile = (index: number) => {
    setFiles(prev => prev.filter((_, i) => i !== index))
    setResult(null)
  }

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    addFiles(e.dataTransfer.files)
  }, [addFiles])

  const onFileInput = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) addFiles(e.target.files)
    e.target.value = ""
  }

  const clientErrors = files.flatMap(f => {
    const errs: string[] = []
    if (!ACCEPTED_TYPES.includes(f.type)) errs.push(`${f.name}: unsupported type (${f.type || "unknown"})`)
    if (f.size > MAX_FILE_SIZE) errs.push(`${f.name}: exceeds 20 MB (${formatBytes(f.size)})`)
    return errs
  })

  const validFiles = files.filter(
    f => ACCEPTED_TYPES.includes(f.type) && f.size <= MAX_FILE_SIZE
  )

  const upload = async () => {
    if (validFiles.length === 0) return
    setUploading(true)
    setError(null)
    setResult(null)
    try {
      const response = await memesApi.uploadMemes(validFiles)
      setResult(response)
      setFiles([])
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed")
    } finally {
      setUploading(false)
    }
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <h1 className="text-2xl font-semibold">Upload Memes</h1>

      <div
        className={`border-2 border-dashed rounded-lg p-10 text-center cursor-pointer transition-colors ${
          dragOver ? "border-blue-500 bg-blue-50" : "border-gray-300 bg-white hover:border-gray-400"
        }`}
        onDragOver={e => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
      >
        <p className="text-gray-500">
          Drag &amp; drop images here, or <span className="text-blue-600 underline">browse</span>
        </p>
        <p className="text-sm text-gray-400 mt-1">
          JPEG, PNG, GIF, WebP, BMP, TIFF · max 20 MB · up to 50 files
        </p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPTED_TYPES.join(",")}
          className="hidden"
          onChange={onFileInput}
        />
      </div>

      {files.length > 0 && (
        <div className="bg-white rounded-lg border divide-y">
          {files.map((f, i) => {
            const invalid = !ACCEPTED_TYPES.includes(f.type) || f.size > MAX_FILE_SIZE
            return (
              <div key={i} className={`flex items-center justify-between px-4 py-2 text-sm ${invalid ? "bg-red-50" : ""}`}>
                <span className={`truncate max-w-xs ${invalid ? "text-red-700" : "text-gray-800"}`}>{f.name}</span>
                <div className="flex items-center gap-3 shrink-0 ml-4">
                  <span className="text-gray-400">{formatBytes(f.size)}</span>
                  <button
                    onClick={() => removeFile(i)}
                    className="text-gray-400 hover:text-red-500"
                    aria-label="Remove"
                  >
                    ✕
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {clientErrors.length > 0 && (
        <ul className="text-sm text-red-600 space-y-1">
          {clientErrors.map((e, i) => <li key={i}>⚠ {e}</li>)}
        </ul>
      )}

      {files.length > 0 && (
        <div className="flex items-center gap-4">
          <button
            onClick={upload}
            disabled={uploading || validFiles.length === 0}
            className="px-5 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {uploading ? "Uploading…" : `Upload ${validFiles.length} file${validFiles.length !== 1 ? "s" : ""}`}
          </button>
          <button
            onClick={() => { setFiles([]); setResult(null); setError(null) }}
            className="text-sm text-gray-500 hover:text-gray-700"
          >
            Clear all
          </button>
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 rounded p-4 text-sm">{error}</div>
      )}

      {result && (
        <div className="space-y-4">
          <div className="flex gap-6 text-sm font-medium">
            <span className="text-green-700">{result.total_accepted} uploaded</span>
            {result.total_failed > 0 && (
              <span className="text-red-600">{result.total_failed} failed</span>
            )}
          </div>

          {result.uploaded.length > 0 && (
            <div className="bg-green-50 border border-green-200 rounded-lg divide-y divide-green-100">
              {result.uploaded.map((f, i) => (
                <div key={i} className="flex items-center justify-between px-4 py-2 text-sm">
                  <span className="text-green-800 truncate max-w-xs">{f.original_filename}</span>
                  <span className="text-green-600 shrink-0 ml-4">{formatBytes(f.size_bytes)}</span>
                </div>
              ))}
            </div>
          )}

          {result.failed.length > 0 && (
            <div className="bg-red-50 border border-red-200 rounded-lg divide-y divide-red-100">
              {result.failed.map((f, i) => (
                <div key={i} className="px-4 py-2 text-sm">
                  <span className="text-red-800 font-medium">{f.original_filename}</span>
                  <span className="text-red-600 ml-2">— {f.reason}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
