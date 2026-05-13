"use client";

import { cn, formatCurrency, formatDate, getStatusColor, getStatusLabel } from "@/lib/utils";
import { MapPin, Package, Truck } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";

interface OrderCardProps {
  order: {
    id: string;
    userId?: string;
    user_id?: string;
    status: string;
    items: Array<{
      id: string;
      name: string;
      image_url?: string;
      imageUrl?: string;
      quantity: number;
      price: number;
    }>;
    total_amount?: number;
    totalAmount?: number;
    shipping_address?: string;
    shippingAddress?: string;
    created_at?: string;
    createdAt?: string;
    tracking_number?: string | null;
    trackingNumber?: string | null;
    carrier?: string | null;
  };
}

export default function OrderCard({ order }: OrderCardProps) {
  const totalAmount = order.total_amount ?? order.totalAmount ?? 0;
  const shippingAddress = order.shipping_address ?? order.shippingAddress ?? "";
  const createdAt = order.created_at ?? order.createdAt ?? "";
  const trackingNumber = order.tracking_number ?? order.trackingNumber;
  const buyerId = order.userId ?? order.user_id;

  const hasLogistics = Boolean(shippingAddress || trackingNumber);

  return (
    <Card
      className={cn(
        "w-full overflow-hidden border border-slate-200/80 bg-card shadow-sm",
        "ring-1 ring-black/[0.04] transition-shadow hover:shadow-md",
        "animate-in fade-in slide-in-from-bottom-2 duration-300"
      )}
    >
      <CardHeader className="space-y-0 border-b border-slate-100 bg-slate-50/60 px-4 py-3 sm:px-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex min-w-0 flex-1 items-start gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-slate-200 bg-background shadow-sm">
              <Package className="h-4 w-4 text-muted-foreground" />
            </div>
            <div className="min-w-0 space-y-1">
              <CardTitle className="text-base font-semibold tracking-tight text-slate-900">
                订单详情
              </CardTitle>
              <CardDescription className="font-mono text-xs text-slate-500">
                #{order.id}
                {buyerId ? (
                  <span className="ml-2 text-slate-400">· 用户 {buyerId}</span>
                ) : null}
              </CardDescription>
            </div>
          </div>
          <Badge
            variant="outline"
            className={cn(
              "shrink-0 border font-medium shadow-none",
              getStatusColor(order.status)
            )}
          >
            {getStatusLabel(order.status)}
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-0 px-0 pb-0 pt-0">
        <div className="flex items-center justify-between border-b border-slate-100 bg-white px-4 py-2.5 sm:px-5">
          <span className="text-[10px] font-semibold uppercase tracking-widest text-slate-400">
            商品明细
          </span>
          <Badge variant="secondary" className="h-5 px-2 text-[10px] font-medium">
            {order.items.length} 件
          </Badge>
        </div>

        <ul className="divide-y divide-slate-100 bg-white">
          {order.items.map((item) => {
            const imgUrl = item.image_url ?? item.imageUrl;
            return (
              <li key={item.id} className="flex items-center gap-3 px-4 py-3 sm:px-5">
                <Avatar className="h-14 w-14 shrink-0 rounded-lg border border-slate-200 shadow-none">
                  {imgUrl ? (
                    <AvatarImage src={imgUrl} alt={item.name} className="object-cover" />
                  ) : null}
                  <AvatarFallback className="rounded-lg bg-slate-100">
                    <Package className="h-6 w-6 text-slate-400" />
                  </AvatarFallback>
                </Avatar>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-slate-900">{item.name}</p>
                  <div className="mt-1 flex flex-wrap items-center gap-2">
                    <Badge variant="outline" className="h-5 border-slate-200 font-mono text-[10px] font-normal text-slate-600">
                      ×{item.quantity}
                    </Badge>
                    <span className="text-xs text-slate-400">
                      单价 {formatCurrency(item.price)}
                    </span>
                  </div>
                </div>
                <span className="shrink-0 text-sm font-semibold tabular-nums text-slate-900">
                  {formatCurrency(item.price * item.quantity)}
                </span>
              </li>
            );
          })}
        </ul>
      </CardContent>

      <Separator className="bg-slate-100" />

      <CardFooter className="flex flex-col items-stretch gap-0 border-t border-slate-100 bg-slate-50/40 px-4 py-0 sm:px-5">
        <div className="space-y-2.5 py-3">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">下单时间</span>
            <span className="font-mono text-slate-700">
              {createdAt ? formatDate(createdAt) : "—"}
            </span>
          </div>

          {hasLogistics ? (
            <>
              <Separator className="my-1 bg-slate-200/80" />
              <p className="text-[10px] font-semibold uppercase tracking-widest text-slate-400">
                配送与追踪
              </p>
              {shippingAddress ? (
                <div className="flex gap-2 rounded-lg border border-slate-200/80 bg-background px-2.5 py-2">
                  <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="text-xs leading-relaxed text-slate-600">{shippingAddress}</span>
                </div>
              ) : null}
              {trackingNumber ? (
                <div className="flex items-center gap-2 rounded-lg border border-slate-200/80 bg-background px-2.5 py-2">
                  <Truck className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  <span className="text-xs text-slate-600">
                    {order.carrier ? `${order.carrier} · ` : ""}
                    <span className="font-mono">{trackingNumber}</span>
                  </span>
                </div>
              ) : null}
            </>
          ) : null}
        </div>

        <Separator className="bg-slate-200/80" />

        <div className="flex items-center justify-between py-3.5">
          <span className="text-sm font-medium text-slate-800">实付金额</span>
          <div className="flex items-center gap-2">
            <Badge variant="secondary" className="hidden font-mono text-[10px] sm:inline-flex">
              CNY
            </Badge>
            <span className="text-lg font-bold tabular-nums tracking-tight text-primary">
              {formatCurrency(totalAmount)}
            </span>
          </div>
        </div>
      </CardFooter>
    </Card>
  );
}
