/**
 * Thin fetch wrapper for `/api/v1`: same-origin cookies, CSRF token on unsafe requests,
 * problem-details errors, one silent refresh after a 401 (spec 4, 10).
 */

import { i18n } from '@/i18n'

export const API_PREFIX = '/api/v1'
const UNSAFE = new Set(['POST', 'PUT', 'PATCH', 'DELETE'])
// A 401 on these paths means "wrong credentials" or "no session yet", not "token expired".
const NO_REFRESH = new Set(['/auth/login', '/auth/refresh', '/auth/first-admin', '/auth/csrf'])

/** The problem document the backend returns (RFC 9457 style). */
export interface Problem {
  type?: string
  code: string
  title: string
  detail: string
  status: number
  correlation_id?: string
  errors?: { loc: string[]; type: string }[]
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly detail: string
  readonly correlationId: string | undefined
  readonly retryAfter: number | undefined

  constructor(problem: Problem, retryAfter?: number) {
    super(problem.title)
    this.name = 'ApiError'
    this.status = problem.status
    this.code = problem.code
    this.detail = problem.detail
    this.correlationId = problem.correlation_id
    this.retryAfter = retryAfter
  }
}

/** The server could not be reached at all (offline, DNS, proxy down). */
export class NetworkError extends Error {
  constructor() {
    super('network')
    this.name = 'NetworkError'
  }
}

let csrfToken: string | null = null
let csrfRequest: Promise<string> | null = null
let refreshRequest: Promise<boolean> | null = null

export function resetApiState(): void {
  csrfToken = null
  csrfRequest = null
  refreshRequest = null
}

function baseHeaders(): Headers {
  const headers = new Headers({ Accept: 'application/json, application/problem+json' })
  headers.set('Accept-Language', i18n.language === 'en' ? 'en' : 'uz')
  return headers
}

async function ensureCsrf(force = false): Promise<string> {
  if (force) {
    csrfToken = null
    csrfRequest = null
  }
  if (csrfToken) return csrfToken
  csrfRequest ??= fetch(`${API_PREFIX}/auth/csrf`, {
    credentials: 'same-origin',
    headers: baseHeaders(),
  })
    .then(async (response) => {
      if (!response.ok) throw await toError(response)
      const body = (await response.json()) as { csrf_token: string }
      csrfToken = body.csrf_token
      return body.csrf_token
    })
    .catch((error: unknown) => {
      csrfRequest = null
      throw error instanceof ApiError ? error : new NetworkError()
    })
  return csrfRequest
}

async function toError(response: Response): Promise<ApiError> {
  let problem: Problem
  try {
    problem = (await response.json()) as Problem
    if (typeof problem.code !== 'string') throw new Error('not a problem document')
  } catch {
    problem = { code: 'server_error', title: response.statusText, detail: '', status: response.status }
  }
  const wait = Number(response.headers.get('Retry-After'))
  return new ApiError(problem, Number.isFinite(wait) && wait > 0 ? wait : undefined)
}

async function send(path: string, init: RequestInit, retried: boolean): Promise<Response> {
  const method = (init.method ?? 'GET').toUpperCase()
  const headers = baseHeaders()
  new Headers(init.headers).forEach((value, key) => headers.set(key, value))
  if (init.body instanceof FormData) headers.delete('Content-Type')
  else if (init.body !== undefined && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  if (UNSAFE.has(method)) headers.set('X-CSRF-Token', await ensureCsrf())
  let response: Response
  try {
    response = await fetch(`${API_PREFIX}${path}`, { ...init, method, headers, credentials: 'same-origin' })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error
    throw new NetworkError()
  }
  if (response.ok) return response
  const error = await toError(response)
  if (!retried && error.code === 'csrf_failed') {
    await ensureCsrf(true) // the token or its cookie went stale: fetch a new pair once
    return send(path, init, true)
  }
  if (!retried && error.status === 401 && !NO_REFRESH.has(path) && (await refreshSession())) {
    return send(path, init, true)
  }
  throw error
}

/** Rotate the refresh token once, even when several requests fail at the same moment. */
export function refreshSession(): Promise<boolean> {
  refreshRequest ??= (async () => {
    try {
      await send('/auth/refresh', { method: 'POST' }, true)
      return true
    } catch {
      return false
    } finally {
      refreshRequest = null
    }
  })()
  return refreshRequest
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await send(path, init, false)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const get = <T>(path: string, signal?: AbortSignal) => api<T>(path, { signal: signal ?? null })
export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body: body === undefined ? null : JSON.stringify(body) })
export const postForm = <T>(path: string, body: FormData) => api<T>(path, { method: 'POST', body })
export const patch = <T>(path: string, body: unknown) =>
  api<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
export const put = <T>(path: string, body: unknown) => api<T>(path, { method: 'PUT', body: JSON.stringify(body) })
export const del = <T>(path: string) => api<T>(path, { method: 'DELETE' })

/** A plain-language message for any error, in the active language. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.detail || error.message
  if (error instanceof NetworkError) return i18n.t('errors.network')
  return i18n.t('errors.generic')
}
