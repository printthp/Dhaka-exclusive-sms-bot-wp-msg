import React from 'react';
import { LayoutDashboard, ShoppingCart, Facebook, Truck, Bot, BarChart3 } from 'lucide-react';
import { BarChart, Bar, XAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts';

const adData = [{ name: 'Camp 1', spend: 5000, profit: 12000 }, { name: 'Camp 2', spend: 8000, profit: 7500 }, { name: 'Camp 3', spend: 3000, profit: 9000 }, { name: 'Camp 4', spend: 12000, profit: 25000 }];

const App = () => {
  return (
    <div className="flex h-screen bg-black text-slate-100 font-sans">
      <aside className="w-64 bg-zinc-950 p-6 border-r border-zinc-800">
        <h1 className="text-brand-neon font-bold text-xl mb-10">Automachine</h1>
        <div className="space-y-4"><div className="flex gap-3 text-brand-neon"><LayoutDashboard size={20}/> Dashboard</div><div className="flex gap-3"><Facebook size={20}/> FB Ads</div><div className="flex gap-3"><ShoppingCart size={20}/> Orders</div><div className="flex gap-3"><Truck size={20}/> Pathao</div><div className="flex gap-3"><Bot size={20}/> Bot Control</div></div>
      </aside>
      <main className="flex-1 p-8">
        <h2 className="text-3xl font-bold mb-8">Command Center</h2>
        <div className="grid grid-cols-4 gap-6 mb-8">
          <div className="bg-zinc-950 p-6 rounded-2xl border border-zinc-800"><p className="text-zinc-500 text-xs">Total Spend</p><h3 className="text-2xl font-bold">৳ ২৮,০০০</h3></div>
          <div className="bg-zinc-950 p-6 rounded-2xl border border-zinc-800"><p className="text-zinc-500 text-xs">Total Orders</p><h3 className="text-2xl font-bold">৫৪২</h3></div>
          <div className="bg-zinc-950 p-6 rounded-2xl border border-zinc-800"><p className="text-zinc-500 text-xs">Success Rate</p><h3 className="text-2xl font-bold">৮৯.৫%</h3></div>
          <div className="bg-zinc-950 p-6 rounded-2xl border border-zinc-800"><p className="text-zinc-500 text-xs">Net Profit</p><h3 className="text-2xl font-bold text-brand-neon">৳ ১,৪৫,০০০</h3></div>
        </div>
        <div className="bg-zinc-950 p-8 rounded-3xl border border-zinc-800 h-80">
           <h3 className="text-xl font-bold mb-6 flex gap-2"><BarChart3 className="text-brand-neon"/> Ads Analysis</h3>
           <ResponsiveContainer width="100%" height="80%"><BarChart data={adData}><XAxis dataKey="name" stroke="#52525b"/><Tooltip/><Bar dataKey="profit" radius={[6,6,0,0]}>{adData.map((e,i) => <Cell key={i} fill={e.profit > e.spend ? '#deff9a' : '#ef4444'} />)}</Bar></BarChart></ResponsiveContainer>
        </div>
      </main>
    </div>
  );
};
export default App;
