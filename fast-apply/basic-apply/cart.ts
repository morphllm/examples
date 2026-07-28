export type CartItem = {
  sku: string;
  price: number;
  quantity: number;
};

export function subtotal(items: CartItem[]): number {
  return items.reduce((sum, item) => sum + item.price * item.quantity, 0);
}

export function applyDiscount(total: number, percentOff: number): number {
  return total * (1 - percentOff / 100);
}

export function checkout(items: CartItem[], percentOff: number): number {
  return applyDiscount(subtotal(items), percentOff);
}
