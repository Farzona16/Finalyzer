import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError, NetworkError, api, errorMessage, get, post, resetApiState } from './client'

type Handler = (url: string, init: RequestInit) => Response | Promise<Response>

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': status >= 400 ? 'application/problem+json' : 'application/json', ...headers },
  })
}

function problem(code: string, status: number, headers: Record<string, string> = {}): Response {
  return json({ code, title: code, detail: `detail ${code}`, status, correlation_id: 'c1' }, status, headers)
}

function install(handler: Handler) {
  const calls: { url: string; init: RequestInit }[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string, init: RequestInit = {}) => {
      calls.push({ url, init })
      return handler(url, init)
    }),
  )
  return calls
}

const header = (init: RequestInit, name: string) => new Headers(init.headers).get(name)

describe('api client', () => {
  beforeEach(() => resetApiState())

  it('sends GET requests with cookies, language and no CSRF token', async () => {
    const calls = install(() => json({ ok: true }))
    await expect(get('/version')).resolves.toEqual({ ok: true })
    expect(calls).toHaveLength(1)
    expect(calls[0]?.url).toBe('/api/v1/version')
    expect(calls[0]?.init.credentials).toBe('same-origin')
    expect(header(calls[0]!.init, 'Accept-Language')).toMatch(/^(uz|en)$/)
    expect(header(calls[0]!.init, 'X-CSRF-Token')).toBeNull()
  })

  it('fetches a CSRF token once and sends it on unsafe requests', async () => {
    const calls = install((url) => (url.endsWith('/auth/csrf') ? json({ csrf_token: 'tok' }) : json({ id: 1 })))
    await post('/things', { a: 1 })
    await post('/things', { a: 2 })
    expect(calls.filter((c) => c.url.endsWith('/auth/csrf'))).toHaveLength(1)
    const posts = calls.filter((c) => c.url.endsWith('/things'))
    expect(posts).toHaveLength(2)
    for (const c of posts) {
      expect(header(c.init, 'X-CSRF-Token')).toBe('tok')
      expect(header(c.init, 'Content-Type')).toBe('application/json')
    }
  })

  it('retries once with a fresh token after csrf_failed', async () => {
    let issued = 0
    let attempts = 0
    const calls = install((url, init) => {
      if (url.endsWith('/auth/csrf')) return json({ csrf_token: `tok${++issued}` })
      attempts++
      return header(init, 'X-CSRF-Token') === 'tok2' ? json({ ok: true }) : problem('csrf_failed', 403)
    })
    await expect(post('/things')).resolves.toEqual({ ok: true })
    expect(attempts).toBe(2)
    expect(issued).toBe(2)
    expect(calls).toHaveLength(4)
  })

  it('turns a problem+json body into an ApiError with a Retry-After hint', async () => {
    install(() => problem('rate_limited', 429, { 'Retry-After': '12' }))
    const error = await get('/x').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    const apiError = error as ApiError
    expect(apiError.code).toBe('rate_limited')
    expect(apiError.status).toBe(429)
    expect(apiError.retryAfter).toBe(12)
    expect(apiError.correlationId).toBe('c1')
    expect(errorMessage(apiError)).toBe('detail rate_limited')
  })

  it('copes with an error body that is not a problem document', async () => {
    install(() => new Response('<html>boom</html>', { status: 502 }))
    const error = (await get('/x').catch((e: unknown) => e)) as ApiError
    expect(error).toBeInstanceOf(ApiError)
    expect(error.code).toBe('server_error')
    expect(error.status).toBe(502)
  })

  it('refreshes the session once after a 401 and repeats the request', async () => {
    let refreshed = false
    const calls = install((url, init) => {
      if (url.endsWith('/auth/csrf')) return json({ csrf_token: 'tok' })
      if (url.endsWith('/auth/refresh')) {
        refreshed = true
        return json({ ok: true })
      }
      expect(init.method).toBe('GET')
      return refreshed ? json({ data: 1 }) : problem('unauthorized', 401)
    })
    await expect(get('/me')).resolves.toEqual({ data: 1 })
    expect(calls.filter((c) => c.url.endsWith('/auth/refresh'))).toHaveLength(1)
  })

  it('shares one refresh between simultaneous 401s', async () => {
    let refreshes = 0
    install((url) => {
      if (url.endsWith('/auth/csrf')) return json({ csrf_token: 'tok' })
      if (url.endsWith('/auth/refresh')) {
        refreshes++
        return json({ ok: true })
      }
      return refreshes ? json({ ok: true }) : problem('unauthorized', 401)
    })
    await Promise.all([get('/a'), get('/b'), get('/c')])
    expect(refreshes).toBe(1)
  })

  it('does not loop when the refresh fails', async () => {
    const calls = install((url) => {
      if (url.endsWith('/auth/csrf')) return json({ csrf_token: 'tok' })
      return problem('unauthorized', 401)
    })
    const error = (await get('/me').catch((e: unknown) => e)) as ApiError
    expect(error.status).toBe(401)
    expect(calls.filter((c) => c.url.endsWith('/auth/refresh'))).toHaveLength(1)
    expect(calls.filter((c) => c.url.endsWith('/me'))).toHaveLength(1)
  })

  it('does not try to refresh after a failed login', async () => {
    const calls = install((url) => (url.endsWith('/auth/csrf') ? json({ csrf_token: 'tok' }) : problem('invalid_credentials', 401)))
    await expect(post('/auth/login', {})).rejects.toBeInstanceOf(ApiError)
    expect(calls.some((c) => c.url.endsWith('/auth/refresh'))).toBe(false)
  })

  it('reports an unreachable server as a NetworkError with a plain message', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('failed'))))
    const error = await get('/x').catch((e: unknown) => e)
    expect(error).toBeInstanceOf(NetworkError)
    expect(errorMessage(error)).not.toMatch(/TypeError|failed/)
    expect(errorMessage(error).length).toBeGreaterThan(10)
  })

  it('returns undefined for 204', async () => {
    install(() => new Response(null, { status: 204 }))
    await expect(api('/x', { method: 'GET' })).resolves.toBeUndefined()
  })

  it('gives a generic message for unknown errors', () => {
    expect(errorMessage(new Error('secret internals'))).not.toContain('secret')
  })
})
