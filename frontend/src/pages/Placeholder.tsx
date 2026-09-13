interface Props {
  title: string;
  phase: string;
  description: string;
}

// Honest placeholder: says what is planned and when, instead of a fake screen.
export default function Placeholder({ title, phase, description }: Props) {
  return (
    <section>
      <h1>{title}</h1>
      <div className="card">
        <p className="muted">Planned for {phase}. Not implemented yet.</p>
        <p>{description}</p>
      </div>
    </section>
  );
}
