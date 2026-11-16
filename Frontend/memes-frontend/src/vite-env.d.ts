/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_BACKEND_API_URL: string
  /** Build-time stage: "prod" hides everything a public reader backend does not serve; unset or anything else is dev. */
  readonly VITE_STAGE?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
