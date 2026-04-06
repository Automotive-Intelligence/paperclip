import CountUp from 'react-countup';
import { motion } from 'framer-motion';

type Props = {
  label: string;
  value: number;
  suffix?: string;
};

export default function StatCard({ label, value, suffix = '' }: Props) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="rounded-xl border border-pitborder bg-pitcard p-4 shadow-pit"
    >
      <div className="text-xs uppercase tracking-wide text-pitmuted">{label}</div>
      <div className="mt-2 text-3xl font-semibold text-pittext">
        <CountUp end={value} duration={0.9} decimals={suffix === '%' ? 2 : 0} />
        {suffix}
      </div>
    </motion.div>
  );
}
