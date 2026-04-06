type Props = { status?: string; pulse?: boolean };

export default function StatusDot({ status = 'amber', pulse = true }: Props) {
  const color = status === 'green' ? 'bg-pitgreen' : status === 'red' ? 'bg-pitred' : 'bg-pitamber';
  return (
    <span className="relative inline-flex h-2.5 w-2.5">
      {pulse ? <span className={`absolute inline-flex h-full w-full rounded-full ${color} opacity-70 animate-ping`} /> : null}
      <span className={`relative inline-flex rounded-full h-2.5 w-2.5 ${color}`} />
    </span>
  );
}
