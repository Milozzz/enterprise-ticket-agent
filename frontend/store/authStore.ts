import { create } from 'zustand';

export type UserRole = 'USER' | 'AGENT' | 'MANAGER';

interface AuthState {
  currentRole: UserRole;
  currentUserId: string;
  setRole: (role: UserRole) => void;
  setUserId: (id: string) => void;
}

export const useAuthStore = create<AuthState>((set) => ({
  currentRole: 'AGENT', // 默认客服
  currentUserId: 'agent_001',
  setRole: (role) => set({ currentRole: role }),
  setUserId: (id) => set({ currentUserId: id }),
}));
