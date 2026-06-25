export interface UploadedFile {
  original_filename: string
  saved_as: string
  size_bytes: number
  content_type: string
  status: string
}

export interface FailedFile {
  original_filename: string
  reason: string
}

export interface UploadResponse {
  uploaded: UploadedFile[]
  failed: FailedFile[]
  total_accepted: number
  total_failed: number
}
