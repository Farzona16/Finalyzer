/**
 * The signed-in user, loaded once per page and kept in memory (spec U8).
 */

import { useQuery } from '@tanstack/react-query'
import { create } from 'zustand'

import { ApiError, get, post } from '@/api/client'

export interface Me {
  user: {
    id: string
    email: string
    display_name: string
    role: string
    language: string
    theme: string
    reading_level: string
  }
  workspace: { id: string; name: string; privacy_mode: string; currency: string; language: string }
  permissions: string[]
}

interface SessionState {
  me: Me | null
  setMe: (me: Me | null) => void
}

export const useSession = create<SessionState>((set) => ({
  me: null,
  setMe: (me) => set({ me }),
}))

export function useMeQuery() {
  const setMe = useSession((s) => s.setMe)
  return useQuery({
    queryKey: ['me'],
    queryFn: async ({ signal }) => {
      try {
        const me = await get<Me>('/auth/me', signal)
        setMe(me)
        return me
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) {
          setMe(null)
          return null
        }
        throw error
      }
    },
    retry: false,
  })
}

export async function signOut(): Promise<void> {
  try {
    await post('/auth/logout')
  } finally {
    useSession.getState().setMe(null)
  }
}

export function can(permission: string): boolean {
  return useSession.getState().me?.permissions.includes(permission) ?? false
}
