import type { RecutProposalDetail } from "../../recut/types"

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-gray-500">{label}</dt>
      <dd className="text-sm font-medium">{value}</dd>
    </div>
  )
}

const boxText = (box: { x: number; y: number; w: number; h: number }): string => `${box.x}, ${box.y}, ${box.w} x ${box.h}`

export default function ProposalInfo({ detail }: { detail: RecutProposalDetail }) {
  const { box, detectedBox } = detail
  return (
    <>
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Field label="Confidence" value={detail.confidence === null ? "not scored" : detail.confidence.toFixed(2)} />
        <Field label="Box (x, y, w x h)" value={boxText(box)} />
        <Field label="Detector" value={`${detail.detector} ${detail.detectorVersion}`} />
        <Field label="State" value={detail.state} />
        {detectedBox !== null && <Field label="Adjusted" value={boxText(detectedBox)} />}
      </dl>
      {detectedBox !== null && (
        <p className="mt-2 text-xs text-gray-600">
          Adjusted by hand; the box under &quot;Adjusted&quot; is the detector&apos;s. The confidence refers to the
          detector&apos;s original box.
        </p>
      )}
    </>
  )
}
