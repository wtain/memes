import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { createMemoryRouter, RouterProvider } from "react-router-dom"
import ProfileEditor from "./ProfileEditor"
import { makeCuratedProfile } from "../../curator/fixtures"
import { makeMockApi } from "../../test/mockApi"

// jsdom's AbortSignal is not the one Node's Request accepts, so a completed navigation would throw inside
// react-router. The wrapper drops the signal; the tests never abort a navigation.
const NativeRequest = globalThis.Request
beforeAll(() => {
  globalThis.Request = class extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      if (init?.signal) {
        const { signal: _signal, ...rest } = init
        void _signal
        super(input, rest)
      } else super(input, init)
    }
  }
})
afterAll(() => { globalThis.Request = NativeRequest })

function setup(overrides: Parameters<typeof makeMockApi>[0] = {}) {
  const api = makeMockApi(overrides)
  const router = createMemoryRouter([
    { path: "/", element: <ProfileEditor memesApi={api} imageId="img-1" /> },
    { path: "/other", element: <p>other page</p> },
  ])
  render(<RouterProvider router={router} />)
  return { api, router, user: userEvent.setup() }
}

async function loaded(overrides: Parameters<typeof makeMockApi>[0] = {}) {
  const result = setup(overrides)
  await screen.findByLabelText("Situation")
  return result
}

const situation = () => screen.getByLabelText("Situation") as HTMLTextAreaElement

describe("ProfileEditor", () => {
  it("labels a model profile and shows taxonomy labels and the texts", async () => {
    await loaded({ getCuratedProfile: vi.fn().mockResolvedValue(makeCuratedProfile({ useWhen: "When late." })) })
    expect(screen.getByText("Model profile")).toBeInTheDocument()
    expect(await screen.findByText("Annoyance")).toBeInTheDocument()
    expect(situation().value).toBe("Manager scheduled a late meeting.")
    expect((screen.getByLabelText("Use when") as HTMLTextAreaElement).value).toBe("When late.")
  })

  it("labels a verified profile", async () => {
    await loaded({ getCuratedProfile: vi.fn().mockResolvedValue(makeCuratedProfile({ humanVerified: true })) })
    expect(screen.getByText("Human-verified")).toBeInTheDocument()
  })

  it("shows the needs-review banner", async () => {
    await loaded({ getCuratedProfile: vi.fn().mockResolvedValue(makeCuratedProfile({ humanVerified: true, needsReview: true })) })
    expect(screen.getByText(/The vocabulary changed since this profile was verified/)).toBeInTheDocument()
  })

  it("saves an edited situation and announces it in the same status node", async () => {
    const { api, user } = await loaded()
    const status = screen.getByRole("status")
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
    await user.type(situation(), "!")
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Save" }))
    expect(api.saveCuratedProfile).toHaveBeenCalledWith("img-1", {
      situation: "Manager scheduled a late meeting.!",
      useWhen: null,
      values: { emotion: ["annoyance"], stance: ["sarcasm"], topic: ["work"], tone: [] },
    })
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Saved"))
    expect(screen.getByRole("status")).toBe(status)
  })

  it("announces and shows a pending vector", async () => {
    const pending = "The search vector is pending and will be refreshed by the next embeddings run."
    const { user } = await loaded({ saveCuratedProfile: vi.fn().mockResolvedValue(makeCuratedProfile({ vector: "pending" })) })
    await user.type(situation(), "!")
    await user.click(screen.getByRole("button", { name: "Save" }))
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(`Saved. ${pending}`))
    expect(screen.getByText(pending)).toBeInTheDocument()
  })

  it("removes and adds chips, then discards", async () => {
    const { user } = await loaded()
    await screen.findByText("Annoyance")
    await user.click(screen.getByRole("button", { name: "Remove emotion: Annoyance" }))
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled()
    await user.click(screen.getByRole("button", { name: /Add.*tone/ }))
    await user.click(await screen.findByRole("option", { name: "Wholesome" }))
    expect(screen.getByText("Wholesome")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Discard changes" }))
    expect(screen.getByText("Annoyance")).toBeInTheDocument()
    expect(screen.queryByText("Wholesome")).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
    expect(screen.getByRole("heading", { name: "Profile" })).toHaveFocus()
  })

  it("offers confirm for model and needs-review profiles, not for a fresh verified one or with edits", async () => {
    const { api, user } = await loaded()
    await user.type(situation(), "!")
    expect(screen.queryByRole("button", { name: "Confirm as correct" })).not.toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Discard changes" }))
    await user.click(screen.getByRole("button", { name: "Confirm as correct" }))
    expect(api.confirmCuratedProfile).toHaveBeenCalledWith("img-1")
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("Confirmed as correct"))
    expect(screen.queryByRole("button", { name: "Confirm as correct" })).not.toBeInTheDocument()
  })

  it("offers confirm for a needs-review profile", async () => {
    await loaded({ getCuratedProfile: vi.fn().mockResolvedValue(makeCuratedProfile({ humanVerified: true, needsReview: true })) })
    expect(screen.getByRole("button", { name: "Confirm as correct" })).toBeInTheDocument()
  })

  it("hides confirm for a fresh verified profile", async () => {
    await loaded({ getCuratedProfile: vi.fn().mockResolvedValue(makeCuratedProfile({ humanVerified: true })) })
    expect(screen.queryByRole("button", { name: "Confirm as correct" })).not.toBeInTheDocument()
  })

  it("resets only after the inline confirm step", async () => {
    const { api, user } = await loaded()
    await user.click(screen.getByRole("button", { name: "Reset to model" }))
    expect(api.resetCuratedProfile).not.toHaveBeenCalled()
    await user.click(screen.getByRole("button", { name: "Cancel" }))
    expect(api.resetCuratedProfile).not.toHaveBeenCalled()
    await user.click(screen.getByRole("button", { name: "Reset to model" }))
    await user.click(screen.getByRole("button", { name: "Confirm reset" }))
    expect(api.resetCuratedProfile).toHaveBeenCalledWith("img-1")
    expect(await screen.findByText("No profile yet")).toBeInTheDocument()
    expect(screen.getByRole("status")).toHaveTextContent("Reset. The next batch run re-extracts this profile.")
    expect(screen.queryByRole("button", { name: "Reset to model" })).not.toBeInTheDocument()
  })

  it("offers a new profile for an unprofiled meme", async () => {
    const { user } = await loaded({ getCuratedProfile: vi.fn().mockResolvedValue(makeCuratedProfile({ exists: false, situation: null, values: { emotion: [], stance: [], topic: [], tone: [] } })) })
    expect(screen.getByText("No profile yet")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Reset to model" })).not.toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled()
    await user.type(situation(), "New")
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled()
  })

  it("shows a failed save in an alert without the texts and keeps edits and focus", async () => {
    const { user } = await loaded({ saveCuratedProfile: vi.fn().mockRejectedValue(new Error("down")) })
    await user.type(situation(), "!")
    await user.click(screen.getByRole("button", { name: "Save" }))
    const alert = await screen.findByRole("alert")
    expect(alert).toHaveTextContent("Couldn't save the profile")
    expect(alert).not.toHaveTextContent("Manager")
    expect(situation().value).toBe("Manager scheduled a late meeting.!")
    await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toHaveFocus())
  })

  it("blocks leaving with unsaved edits; Stay keeps the page", async () => {
    const { router, user } = await loaded()
    await user.type(situation(), "!")
    await act(async () => { await router.navigate("/other") })
    expect(screen.getByText("You have unsaved changes.")).toBeInTheDocument()
    expect(router.state.location.pathname).toBe("/")
    await user.click(screen.getByRole("button", { name: "Stay" }))
    expect(screen.queryByText("You have unsaved changes.")).not.toBeInTheDocument()
    expect(router.state.location.pathname).toBe("/")
    expect(situation().value).toBe("Manager scheduled a late meeting.!")
  })

  it("Leave without saving completes the navigation", async () => {
    const { router, user } = await loaded()
    await user.type(situation(), "!")
    await act(async () => { await router.navigate("/other") })
    await user.click(screen.getByRole("button", { name: "Leave without saving" }))
    await waitFor(() => expect(router.state.location.pathname).toBe("/other"))
    expect(screen.getByText("other page")).toBeInTheDocument()
  })

  it("does not block navigation without edits", async () => {
    const { router } = await loaded()
    await act(async () => { await router.navigate("/other") })
    expect(router.state.location.pathname).toBe("/other")
  })

  it("gives every button a touch-sized height", async () => {
    const { user } = await loaded()
    await user.type(situation(), "!")
    for (const button of screen.getAllByRole("button").filter((b) => !b.hasAttribute("data-chip-remove"))) {
      expect(button.className).toMatch(/min-h-11/)
    }
  })

  it("shows a load failure in an alert", async () => {
    setup({ getCuratedProfile: vi.fn().mockRejectedValue(new Error("down")) })
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load the profile")
  })
})
