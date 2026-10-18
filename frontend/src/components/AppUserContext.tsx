'use client';
import {createContext,useContext} from 'react';
import type {AuthUser} from '@/lib/api';
export const UserContext=createContext<AuthUser|null>(null);
export function useAppUser(){const user=useContext(UserContext);if(!user)throw new Error('Authenticated app context required');return user;}
