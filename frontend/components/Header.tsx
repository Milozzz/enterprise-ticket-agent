"use client";

import { useAuthStore, UserRole } from "@/store/authStore";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import Link from "next/link";
import { ShieldCheck, User, Headset, Sparkles, LayoutDashboard } from "lucide-react";
import { Button } from "@/components/ui/button";

export function Header() {
  const { currentRole, setRole } = useAuthStore();

  const roleConfig = {
    USER: { label: "普通用户", icon: User, color: "text-blue-500", bg: "bg-blue-50" },
    AGENT: { label: "客服人员", icon: Headset, color: "text-green-500", bg: "bg-green-50" },
    MANAGER: { label: "财务主管", icon: ShieldCheck, color: "text-purple-500", bg: "bg-purple-50" },
  };

  return (
    <header className="sticky top-0 z-50 w-full shrink-0 border-b border-slate-200/90 bg-slate-50/90 backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-[1920px] items-center justify-between px-4 sm:px-6">
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-slate-200 bg-background shadow-sm ring-1 ring-black/[0.04]">
            <Sparkles className="h-[18px] w-[18px] text-primary" />
          </div>
          <div className="flex flex-col gap-0.5">
            <span className="text-sm font-bold leading-none tracking-tight text-slate-900">AI Enterprise</span>
            <span className="text-[10px] font-semibold uppercase tracking-[0.2em] text-slate-500">
              Agent System
            </span>
          </div>
        </div>

        <div className="flex items-center gap-4">
          <Button variant="ghost" size="sm" className="hidden sm:inline-flex text-slate-600" asChild>
            <Link href="/dashboard" className="inline-flex items-center gap-2">
              <LayoutDashboard className="h-4 w-4" />
              运营监控
            </Link>
          </Button>
          <div className="hidden md:flex items-center gap-2 mr-2">
            <div className="h-2 w-2 rounded-full bg-green-500 animate-pulse" />
            <span className="text-[11px] font-medium text-muted-foreground uppercase tracking-wider">System Online</span>
          </div>
          
          <div className="flex items-center gap-3 pl-4 border-l">
            <Select
              value={currentRole}
              onValueChange={(value) => setRole(value as UserRole)}
            >
              <SelectTrigger className="w-[130px] h-9 bg-muted/50 border-none hover:bg-muted transition-colors">
                <SelectValue placeholder="选择身份" />
              </SelectTrigger>
              <SelectContent align="end" className="w-[180px]">
                {(Object.entries(roleConfig) as [UserRole, typeof roleConfig.USER][]).map(([role, config]) => (
                  <SelectItem key={role} value={role} className="cursor-pointer">
                    <div className="flex items-center gap-2 py-1">
                      <config.icon className={`w-4 h-4 ${config.color}`} />
                      <span className="font-medium text-sm">{config.label}</span>
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>
    </header>
  );
}
