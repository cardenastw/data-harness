import {
  BarChart,
  Bar,
  LineChart,
  Line,
  PieChart,
  Pie,
  AreaChart,
  Area,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Cell,
} from "recharts";
import type { ChartConfig } from "../types";

interface Props {
  config: ChartConfig;
}

const DEFAULT_COLORS = [
  "#2563eb",
  "#7c3aed",
  "#db2777",
  "#ea580c",
  "#65a30d",
  "#0d9488",
  "#dc2626",
  "#4f46e5",
];

export default function ChartRenderer({ config }: Props) {
  const colors = config.colors || DEFAULT_COLORS;
  const { data, xAxis, yAxis, title, xLabel, yLabel } = config;

  const common = (
    <>
      <CartesianGrid strokeDasharray="3 3" />
      <XAxis
        dataKey={xAxis}
        label={xLabel ? { value: xLabel, position: "insideBottom", offset: -5 } : undefined}
      />
      <YAxis
        label={yLabel ? { value: yLabel, angle: -90, position: "insideLeft" } : undefined}
      />
      <Tooltip />
      <Legend />
    </>
  );

  let chart;

  switch (config.chartType) {
    case "bar":
      chart = (
        <BarChart data={data}>
          {common}
          <Bar dataKey={yAxis} fill={colors[0]} />
        </BarChart>
      );
      break;

    case "line":
      chart = (
        <LineChart data={data}>
          {common}
          <Line
            type="monotone"
            dataKey={yAxis}
            stroke={colors[0]}
            strokeWidth={2}
          />
        </LineChart>
      );
      break;

    case "area":
      chart = (
        <AreaChart data={data}>
          {common}
          <Area
            type="monotone"
            dataKey={yAxis}
            stroke={colors[0]}
            fill={colors[0]}
            fillOpacity={0.3}
          />
        </AreaChart>
      );
      break;

    case "pie":
      chart = (
        <PieChart>
          <Pie
            data={data}
            dataKey={yAxis}
            nameKey={xAxis}
            cx="50%"
            cy="50%"
            outerRadius={100}
            label
          >
            {data.map((_, i) => (
              <Cell key={i} fill={colors[i % colors.length]} />
            ))}
          </Pie>
          <Tooltip />
          <Legend />
        </PieChart>
      );
      break;

    case "scatter":
      chart = (
        <ScatterChart>
          {common}
          <Scatter data={data} fill={colors[0]} />
        </ScatterChart>
      );
      break;

    default:
      return <div>Unsupported chart type: {config.chartType}</div>;
  }

  return (
    <div className="chart-container">
      <h4 className="chart-title">{title}</h4>
      <ResponsiveContainer width="100%" height={300}>
        {chart}
      </ResponsiveContainer>
    </div>
  );
}
